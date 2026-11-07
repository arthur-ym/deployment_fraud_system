# How `fraud-api.yaml` works

This file is a CloudFormation template. It describes the AWS resources for one fraud-detection API server. CloudFormation reads the file and creates those resources together as one **stack**. Deleting the stack deletes the resources the stack created.

The template does not contain the model or the Python API. It starts an EC2 virtual machine, installs Docker, pulls the image named by `DockerImage` (by default `arthurym25/deployment-fraud-system:prod`) from a private Docker Hub repository, and runs that image on port 8000. An Elastic IP stays on the machine, so the address does not change when the instance is replaced. The running container writes one JSON log line per `/predict` call and stores one JSON object per prediction in S3. The template turns those log lines into metrics and graphs.

Acronyms used below are listed in [Glossary](#glossary).

```text
You pass parameters
        |
        v
CloudFormation creates the stack
        |
        +-- security group     who may open port 22 and port 8000
        +-- IAM role           what the instance is allowed to call in AWS
        +-- log group          where container logs go
        +-- metric filters     counts of requests and failures
        +-- predictions bucket private store for each prediction
        +-- SNS topic          where alarms are sent
        +-- EC2 instance       the machine that runs the container
        +-- Elastic IP         the address clients keep using
        +-- three alarms       CPU, status checks, and server errors
        +-- dashboard          request graphs, host graphs, and recent logs
        |
        v
Outputs: public IP, predict URL, log group, topic, dashboard, bucket name
```

## The shape of a template

A template is YAML with a few fixed sections. This file uses these:

| Section | Lines | What it is |
|---|---|---|
| `AWSTemplateFormatVersion` | 1 | Always `2010-09-09`. It marks the file as a CloudFormation template. |
| `Description` | 2–6 | A sentence CloudFormation shows next to the stack. |
| `Parameters` | 8–70 | Values you fill in at deploy time. The template stays the same; the values change. |
| `Conditions` | 72–73 | A yes/no test used to skip a resource. |
| `Resources` | 75–579 | The AWS objects to create. This is the body of the template. |
| `Outputs` | 581–608 | Values CloudFormation prints after the stack succeeds, so you do not have to hunt for them in the console. |

Each resource has a **logical ID**, a **type**, and **properties**. In this block, `FraudApiLogGroup` is the logical ID and `AWS::Logs::LogGroup` is the type:

```yaml
FraudApiLogGroup:
  Type: AWS::Logs::LogGroup
  Properties:
    LogGroupName: !Sub "/fraud-detection/${Environment}/api"
    RetentionInDays: 30
```

The logical ID is the name other parts of the template use. The type is the AWS service object. Properties are its settings.

## Functions you will see everywhere

CloudFormation adds functions that YAML itself does not have. They look like `!Ref` or `!Sub`.

- `!Ref KeyName` inserts the value of the `KeyName` parameter.
- `!Ref FraudApiInstance` inserts the instance ID. `!Ref FraudApiEip` inserts the Elastic IP address. For most resources, `!Ref` returns the main ID.
- `!GetAtt FraudApiEip.AllocationId` reads one attribute of a resource. The template uses `!GetAtt` when the value it needs is not the main ID, including log group and bucket ARNs.
- `!Sub "${AWS::StackName}-fraud-api"` builds a string and replaces `${...}` with a value. `AWS::StackName`, `AWS::Region`, and `AWS::AccountId` are provided by CloudFormation when the stack is created.
- `!Equals` and `!Not` are comparisons. They are used in the condition below.
- `Fn::Base64` encodes the startup script. EC2 requires user data to be Base64.

`{{resolve:ssm:...}}` on the instance `ImageId` is a dynamic reference. At deploy time CloudFormation looks up the current Amazon Linux 2023 AMI ID from Systems Manager. The template does not hard-code an AMI that will go out of date.

## Parameters

Parameters are the knobs. Anything with a `Default` can be left alone.

| Parameter | Default | Used for | Why used? |
|---|---|---|---|
| `KeyName` | none, you must pass it | Name of an EC2 key pair already in this region. The instance installs that public key for the `ec2-user` login. | The machine is a normal Linux box. If the container fails to start, SSH is how you get on it and read logs. CloudFormation cannot invent a key, so you name one that already exists. |
| `SSHLocation` | `127.0.0.1/32` | The only IP range allowed to open port 22. The default is the machine's own loopback address, so the public internet cannot SSH in. Pass your address, such as `203.0.113.4/32`, when you need a shell. | Port 22 on a public server is a common attack target. The template still has an SSH rule, but it stays closed until you opt in. |
| `ApiAllowedCidr` | `0.0.0.0/0` | The IP range allowed to call the API on port 8000. | This is the product: clients POST to `/predict`. The security group has to let that traffic in. `0.0.0.0/0` means any client on the internet. Narrow it if only one office or one other service should be able to call the model. |
| `InstanceType` | `t3.small` | CPU and memory size of the virtual machine. | The fraud model and the API process need a fixed amount of CPU and RAM. `t3.small` is the small default. Change this to a larger x86_64 type if predictions are slow, without editing the rest of the template. The Amazon Linux image is x86_64, so an Arm type such as `t4g.small` would not boot. |
| `DockerHubSecretId` | `fraud-api/dockerhub` | Name of a Secrets Manager secret you create before the deploy. The secret value is JSON with keys `username` and `token`. | The image is private on Docker Hub. The instance has to log in before `docker pull`. The token stays in Secrets Manager instead of being written into this file or the startup script. |
| `DockerImage` | `arthurym25/deployment-fraud-system:prod` | The Docker image and tag the start script pulls and runs. The same string is passed to the container as `MODEL_VERSION`. | CI publishes `dev`, `stg`, `sha-<commit>`, and `prod`. It does not publish `latest`. Dev and staging deploys pass `:dev` and `:stg`. Those tags move, and the pipeline restarts the container on the existing instance. Production passes `sha-<commit>`, so each production release changes this value and replaces the instance. Each stored prediction records this value. |
| `AlarmEmail` | empty | Email address subscribed to the alarm topic. Empty means no subscription. SNS still asks you to confirm the address before it delivers mail. | The stack watches CPU, EC2 status checks, and HTTP 5xx responses. This is who gets told. Leave it empty when nobody needs those emails. |
| `PredictionRetentionDays` | `365` | How long S3 keeps objects under `predictions/` before deleting them. Allowed range is 1 to 3650 days. | Prediction files accumulate one object per call. This cap is the cost control. Old files expire on their own. |
| `Environment` | none, you must pass it | `dev`, `stg`, or `prod`. The log group is `/fraud-detection/<environment>/api` and custom metrics use the namespace `FraudApi/<environment>`. | Three stacks in one region need different CloudWatch names. The same log group and metric namespace would mix dev, staging, and production on one dashboard. |

`AllowedPattern` is a regex. CloudFormation rejects the deploy if a value does not match, before it creates anything. `Environment` uses `AllowedValues` instead: `dev`, `stg`, or `prod`.

## Conditions

```yaml
HasAlarmEmail: !Not [!Equals [!Ref AlarmEmail, ""]]
```

This is true when `AlarmEmail` is not an empty string. `FraudApiAlarmSubscription` has `Condition: HasAlarmEmail`, so the email subscription is created only when you pass an address. The SNS topic itself is always created.

## Resources, in the order they matter

CloudFormation may create independent resources in parallel. `!Ref` and `!GetAtt` tell it when one resource must wait for another. `DependsOn` is an extra explicit wait.

### Security group

`FraudApiSecurityGroup` is the firewall for the instance. It allows inbound TCP 22 from `SSHLocation` and inbound TCP 8000 from `ApiAllowedCidr`. A security group allows return traffic for those connections, so the template does not list outbound rules. The instance can still reach Docker Hub, Secrets Manager, CloudWatch, and S3.

### IAM role and instance profile

`FraudApiEC2Role` is the identity the instance uses when it calls AWS. The trust policy (`AssumeRolePolicyDocument`) says only the EC2 service may assume this role. That is what attaches the role to a virtual machine.

The role can do five things:

1. `AmazonSSMManagedInstanceCore` lets you open a shell through Systems Manager Session Manager, which is useful while SSH is closed.
2. `secretsmanager:GetSecretValue` on `fraud-api/dockerhub-*` in this account and region. Secrets Manager appends a random suffix to the real ARN, which is why the ARN ends in `-*`.
3. `cloudformation:SignalResource` on this stack, so the startup script can tell CloudFormation that boot finished or failed.
4. Write to the log group `FraudApiLogGroup` and to streams inside it.
5. `s3:PutObject` only on `predictions/*` in this stack's bucket. The role cannot list, read, or delete those objects, and it cannot write anywhere else in the bucket.

`FraudApiInstanceProfile` is a wrapper EC2 requires. The instance property `IamInstanceProfile` points at the profile, and the profile points at the role.

Creating a role needs an extra confirmation on deploy: `--capabilities CAPABILITY_IAM`.

### Log group, metric filters, and alarms

`FraudApiLogGroup` is CloudWatch log group `/fraud-detection/${Environment}/api`, kept for 30 days. For the `prod` stack that path is `/fraud-detection/prod/api`. The container writes a stream named `fraud-api` inside it. Each environment has its own group, so three stacks in the same region do not share logs.

Each `/predict` call prints one JSON object to that stream. A **metric filter** is a rule on the log group: when a line matches, CloudWatch adds 1 to a metric in the `FraudApi/${Environment}` namespace. The `prod` stack publishes to `FraudApi/prod`. The filters do not set a default value, so uvicorn's plain-text lines do not publish zeros. The six filters are:

| Filter | Metric | Matches |
|---|---|---|
| `FraudApiRequestCountFilter` | `RequestCount` | `event` is `request` |
| `FraudApiClientErrorFilter` | `ClientErrorCount` | that line, and HTTP status 400–499 |
| `FraudApiServerErrorFilter` | `ServerErrorCount` | that line, and HTTP status 500–599 |
| `FraudApiInvalidFeaturesFilter` | `InvalidFeaturesCount` | `error_category` is `invalid_features` |
| `FraudApiModelErrorFilter` | `ModelErrorCount` | `error_category` is `model_error` |
| `FraudApiExceptionFilter` | `ExceptionCount` | `error_category` is `exception` |

A missing feature is both a client error and an invalid-features count. A model failure is both a server error and a model-error count. The failure rate uses only the status counts, so those categories are not added a second time.

`FraudApiAlarmTopic` is an SNS topic named from the stack, for example `my-stack-fraud-api-alarms`.

`FraudApiCpuAlarm` fires when average CPU is above 80% for two periods of 5 minutes. Missing data does not fire it.

`FraudApiStatusAlarm` fires when EC2 reports a failed status check twice in a row, one minute apart. Missing data fires it, because a silent instance is treated as unhealthy.

`FraudApiServerErrorAlarm` fires when `ServerErrorCount` sums to at least 1 in a 5-minute period. Missing data does not fire it, so a quiet API is not an incident.

All three alarms publish to the SNS topic. Email is delivered only if you set `AlarmEmail` and confirm the subscription.

### Dashboard

`FraudApiDashboard` is one CloudWatch page named from the stack, for example `my-stack-fraud-api`. The top row is request volume and failed requests (HTTP 4xx and HTTP 5xx). The next row is the failure rate and the error categories (invalid or missing features, model errors, and unhandled exceptions). Below that are the CPU, network, and status-check graphs, plus a table of the latest request logs (`request_id`, status, duration, category, and error message).

The request graphs use a period of one hour and the statistic Sum. The control at the top of the dashboard is the time range: one hour, one day, one week, or a custom range. That is how you look at the same metrics by hour, day, or week. You do not change the template to do that.

The failure rate is metric math, not a stored metric: `100 * (client errors + server errors) / requests` when the request count is greater than 0, and 0 otherwise.

### Predictions bucket

`FraudApiPredictionsBucket` is a private S3 bucket. CloudFormation generates the name, so it is unique to this stack and stays lowercase. The bucket has four protections:

- **Block Public Access** is on. These four switches (`BlockPublicAcls`, `BlockPublicPolicy`, `IgnorePublicAcls`, `RestrictPublicBuckets`) stop a public ACL or a public bucket policy from exposing the files.
- Object ownership is `BucketOwnerEnforced`, so ACLs are disabled and the bucket owner owns every object.
- Default encryption is **SSE-S3**: S3 encrypts every object with AES-256 using a key that S3 manages. The instance does not need a KMS permission.
- `FraudApiPredictionsBucketPolicy` denies every request that is not HTTPS.

The objects are a few kilobytes. Moving objects that small to Infrequent Access costs more than it saves, because that class has a minimum billable size and a retrieval fee. They stay in Standard storage. The lifecycle rule `ExpirePredictions` deletes keys under `predictions/` after `PredictionRetentionDays` (365 by default) and aborts an unfinished multipart upload after 7 days. Versioning is off, because each prediction is written once under its own key.

### The instance

`FraudApiInstance` is one EC2 machine:

- Amazon Linux 2023, looked up from SSM.
- Your key pair and instance type.
- A public IPv4 address on the default network interface, plus an Elastic IP (`FraudApiEip`) associated with the instance. Clients and SSH use the Elastic IP. That address is what `PublicIp` prints, and it stays when a later update replaces the instance.
- The security group attached to that interface.
- A 30 GB encrypted gp3 disk as `/dev/xvda`.
- IMDSv2 required (`HttpTokens: required`). The instance metadata service is how software on the machine gets temporary credentials for the IAM role. Version 2 is the current requirement.
- `HttpPutResponseHopLimit: 2`. The Docker container is a second network hop. A hop limit of 1 would let the host read the role credentials and would stop the process inside the container from doing so. The API needs that second hop to call `s3:PutObject`. The Docker log driver runs on the host, so it does not need the extra hop.

`CreationPolicy` tells CloudFormation to wait until the machine reports success. The wait is 15 minutes (`PT15M`). The startup script sends that report with `cfn-signal`. If the script exits with an error, the trap sends a failure signal and the stack rolls back. If no signal arrives in 15 minutes, the stack fails the same way.

`DependsOn: FraudApiLogGroup` makes the log group exist before the instance boots, because the container is configured with `awslogs-create-group=false` and will not create the group itself.

### Startup script

`UserData` is a bash script that runs once as root on first boot. It installs Docker, writes `/usr/local/bin/start-fraud-api.sh`, runs that script, and then sends `cfn-signal`. CloudFormation substitutes `${AWS::StackName}`, `${DockerHubSecretId}`, `${DockerImage}`, `${FraudApiPredictionsBucket}`, and the other `${...}` values before the instance sees the script. The `{{.State.Running}}` braces are left for the `docker inspect` format string; `!Sub` only replaces `${...}`.

Changing this script replaces the instance. The Elastic IP moves to the new machine, so `PublicIp` and `PredictUrl` stay the same. Dev and staging pass a `DockerImage` tag that does not change between commits (`:dev` or `:stg`), so those deploys leave the instance in place. After the stack update, the pipeline runs `start-fraud-api.sh` through Systems Manager. The script pulls the new image before it removes the current container, so a failed pull leaves the API up. Requests fail only for the few seconds it takes to start the new container.

Production passes `sha-<commit>`. That changes the script on every release, so production still replaces the instance. The Elastic IP stays, and the API is down while the new machine boots. Publish the image first, including the tag this stack pulls.

The boot script does this:

1. Install Docker, `jq`, and `aws-cfn-bootstrap` (the package that provides `cfn-signal`).
2. Start Docker.
3. Write `start-fraud-api.sh` and make it executable.
4. Run that script. If it exits with an error, signal failure.
5. Signal success.

`start-fraud-api.sh` does this, both on first boot and when Systems Manager runs it later:

1. Read the Docker Hub username and token from Secrets Manager. Command tracing stays off so the token is not written to the log.
2. `docker login` and pull the image, then log out and delete the secret file and Docker config.
3. Remove the current container and run a new one named `fraud-detection-api`, with `--restart unless-stopped`, publishing port 8000, and sending logs to the CloudWatch group. It passes three environment variables: `PREDICTIONS_BUCKET` (this stack's bucket), `AWS_DEFAULT_REGION`, and `MODEL_VERSION` (the `DockerImage` value).
4. Wait up to about 30 seconds for the container to be running. This script does not send `cfn-signal`.

`${!DOCKER_USER}` is bash indirect expansion, not CloudFormation. `!Sub` turns it into `${DOCKER_USER}` in the script that actually runs. The leading `!` tells `!Sub` to leave that dollar expression alone.

## What each prediction writes

The code that does this is `src/telemetry.py`, called from `src/inference.py`. The template does not run it. The metric filters and the bucket are built for this shape.

Health checks (`GET /`) are not counted. Only `POST /predict` is. The response body is still `{"prediction": <probability>}`. The response header `X-Request-ID` is the id used in the log line and in the S3 object.

The process prints one JSON object:

| Field | Meaning |
|---|---|
| `event` | Always `request` for these lines. |
| `request_id` | Id for this call. |
| `timestamp` | UTC time the record was written. |
| `path` | `/predict`. |
| `http_status` | Status code returned to the client. |
| `duration_ms` | Time spent handling the call. |
| `error_category` | Why it failed, or `none`. |
| `error_message` | The error text, or null on success. |

`error_category` is one of:

| Category | When |
|---|---|
| `none` | The call returned a probability. |
| `invalid_features` | A required feature is missing, or a value is not numeric. HTTP 400. |
| `client_error` | Any other HTTP 4xx, including a body that is not the expected JSON object. |
| `model_error` | The model raised while computing the probability. HTTP 500. |
| `server_error` | The model is not loaded, or another HTTP 5xx that is not a model error or an unhandled exception. |
| `exception` | An exception escaped the endpoint. HTTP 500. |

When `PREDICTIONS_BUCKET` is set, the same call writes `predictions/dt=YYYY-MM-DD/<request_id>.json`. The date is the UTC date of the call, so later jobs can read one day at a time. The object contains `request_id`, `timestamp`, `http_status`, `status` (`success` or `error`), `duration_ms`, `fraud_probability`, `predicted_class`, `model_version`, `error_category`, `error_message`, and `features`.

`predicted_class` is 1 when `fraud_probability` is at least 0.5, and 0 otherwise. That cutoff is only in the stored file. The HTTP response is still the probability alone. On a failed call both fields are null.

`features` is the `X` object the client sent. Those fields are the model's engineered inputs (rates, aggregates, and `amount_cents` in `src/features.py`), not a card number, name, or email address, so the record keeps them for later analysis. A call that fails before the body is accepted stores `features` as null.

If the S3 write fails, the API still returns its normal response. A second log line with `event` set to `storage_error` records the reason. That line does not match the request metric filters. Local runs and tests leave `PREDICTIONS_BUCKET` unset and skip the upload.

## Outputs

Outputs are the values CloudFormation prints on the stack's Outputs tab after the deploy succeeds. You use them instead of searching the console for the instance, the log group, the dashboard, or the bucket. They are not inputs. Changing an output in the file does not change the running system; it only changes what gets printed next time.

| Output | What you get | Why you use it |
|---|---|---|
| `InstanceId` | The EC2 id, such as `i-0abc123`. | The console, the alarms, and the AWS CLI all refer to the machine by this id. You need it to stop the instance, open a Session Manager shell, or check why the stack is still waiting on the boot signal. |
| `PublicIp` | The Elastic IP attached to the instance. | This is the address of the server on the internet. SSH uses it (`ec2-user@<public-ip>`). CloudFormation moves this address to the new machine when a template update replaces the instance, so clients keep the same URL. |
| `PredictUrl` | `http://<public-ip>:8000/predict` | This is the fraud API. A client POSTs a JSON body of features here and gets a prediction back. It is the address you give to a notebook, a test script, or another service. |
| `LogGroupName` | `/fraud-detection/<environment>/api`, such as `/fraud-detection/prod/api` | Container stdout and stderr go here, in a stream named `fraud-api`. Open this log group in CloudWatch when a request fails or the process crashes. The group is kept for 30 days. |
| `AlarmTopicArn` | The ARN of the SNS topic, such as `arn:aws:sns:eu-central-1:511743255923:my-stack-fraud-api-alarms`. | CPU, status-check, and server-error alarms publish to this topic. Use the ARN to add another subscriber later, such as a second email or a chat webhook, without editing the alarms. |
| `DashboardName` | The CloudWatch dashboard name, such as `my-stack-fraud-api`. | One page with request volume, failures, error categories, host health, and the latest API logs. Search this name in CloudWatch dashboards instead of building the graphs yourself. |
| `PredictionsBucketName` | The generated bucket name. | Where the JSON prediction records are. Use it with the AWS CLI or a later analysis job. The instance can only write under `predictions/`. |

## What must exist before a deploy

CloudFormation will not create these for you:

- An EC2 key pair in the same region, passed as `KeyName`.
- A Secrets Manager secret named `fraud-api/dockerhub` (or whatever you pass). The secret string is JSON: `{"username":"...","token":"..."}`.
- Permission to create the resources, including IAM, because of the role. The earlier `ValidateTemplate` denial came from an organization policy, which is separate from whether this file is valid.

`make lint-cloudformation` checks the file locally. It does not create a stack.

## Glossary

Short meanings for the acronyms in this guide and in `fraud-api.yaml`. Each one is tied to what it does for this API.

| Acronym | Stands for | In this template |
|---|---|---|
| AMI | Amazon Machine Image | The disk image the instance boots from. Here it is Amazon Linux 2023, looked up at deploy time so the id does not go stale. |
| API | Application programming interface | The program clients call. This one listens on port 8000 and accepts a POST at `/predict`. |
| ARN | Amazon Resource Name | The unique address of one AWS object, used in the IAM policy and printed for the alarm topic. Example: `arn:aws:logs:eu-central-1:123456789012:log-group:/fraud-detection/prod/api`. |
| AWS | Amazon Web Services | The cloud these resources run in: the machine, the logs, the alarms, the bucket, and the secret. |
| CIDR | Classless Inter-Domain Routing | A way to write a range of IP addresses. `203.0.113.4/32` is one address. `0.0.0.0/0` is every address. `SSHLocation` and `ApiAllowedCidr` use this form. |
| CI | Continuous integration | The pipeline that builds and publishes the Docker image tags `dev`, `stg`, `sha-<commit>`, and `prod`. |
| CLI | Command-line interface | The `aws` command used on your laptop and inside the startup script. |
| CPU | Central processing unit | The compute the instance spends. The CPU alarm fires when average use stays above 80%. |
| EBS | Elastic Block Store | The virtual hard disk. This instance has one 30 GB encrypted `gp3` volume. |
| EC2 | Elastic Compute Cloud | AWS virtual machines. `FraudApiInstance` is one EC2 instance. |
| GB | Gigabyte | Size of that disk: 30 GB. |
| HTTP | Hypertext Transfer Protocol | The protocol a client uses to call `http://<public-ip>:8000/predict`. This stack does not put HTTPS in front of it. S3 writes still use HTTPS, and the bucket denies anything else. |
| IAM | Identity and Access Management | Users, roles, and permissions. The instance gets a role so it can read the Docker Hub secret, write logs, store predictions, and signal the stack. |
| ID | Identifier | The short name AWS assigns, such as the instance id `i-0abc123`. |
| IMDS | Instance Metadata Service | A link from software on the machine to EC2, at `169.254.169.254`. The instance uses it to fetch temporary credentials for its IAM role. |
| IMDSv2 | Instance Metadata Service version 2 | The current IMDS. `HttpTokens: required` means the instance refuses the older version. The hop limit is 2 so the container can use those credentials. |
| IP | Internet Protocol address | The numeric address of a machine. `PublicIp` is the Elastic IP clients and SSH use. |
| IPv4 | Internet Protocol version 4 | The familiar address form, such as `203.0.113.4`. This template allocates one public IPv4 Elastic IP. |
| JSON | JavaScript Object Notation | The text format of the Docker Hub secret, the dashboard body, each request log line, and each stored prediction. |
| KMS | Key Management Service | AWS encryption keys. The Docker Hub secret uses the default Secrets Manager key, and the bucket uses SSE-S3, so the role needs no extra KMS permission. |
| RAM | Random-access memory | Memory on the instance. `InstanceType` chooses how much CPU and RAM the model gets. |
| S3 | Simple Storage Service | The predictions bucket. Objects are private, encrypted, and expired after `PredictionRetentionDays`. |
| SNS | Simple Notification Service | The alarm mailbox. CPU, status, and server-error alarms publish a message to an SNS topic, and email is one possible subscriber. |
| SSE-S3 | S3-managed server-side encryption | S3 encrypts each prediction object with AES-256. You do not create or pay for a separate key. |
| SSH | Secure Shell | A remote terminal on port 22. `KeyName` is the key, `SSHLocation` is who may connect. |
| SSM | AWS Systems Manager | The service that stores the current Amazon Linux AMI id. Session Manager can open a shell while SSH is closed. Dev and staging deploys use Run Command to run `start-fraud-api.sh` on the existing instance. |
| TCP | Transmission Control Protocol | The network protocol of the two firewall rules: port 22 for SSH and port 8000 for the API. |
| URL | Uniform Resource Locator | A web address. `PredictUrl` is the one clients call. |
| YAML | YAML Ain't Markup Language | The file format of `fraud-api.yaml`. Indentation marks what belongs inside what. |

Block Public Access is the bucket setting that rejects public access even if a later policy tries to allow it. A metric filter is the log rule that turns one JSON request line into a count on the dashboard.
