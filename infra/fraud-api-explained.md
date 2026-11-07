# How `fraud-api.yaml` works

This file is a CloudFormation template. It describes the AWS resources for one fraud-detection API server. CloudFormation reads the file and creates those resources together as one **stack**. Deleting the stack deletes the resources the stack created.

The template does not contain the model or the Python API. It starts an EC2 virtual machine, installs Docker, pulls `arthurym25/deployment-fraud-system:prod` from a private Docker Hub repository, and runs that image on port 8000.

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
        +-- SNS topic          where alarms are sent
        +-- EC2 instance       the machine that runs the container
        +-- two alarms         CPU and status checks
        +-- dashboard          graphs and recent logs
        |
        v
Outputs: public IP, predict URL, log group, topic, dashboard
```

## The shape of a template

A template is YAML with a few fixed sections. This file uses these:

| Section | Lines | What it is |
|---|---|---|
| `AWSTemplateFormatVersion` | 1 | Always `2010-09-09`. It marks the file as a CloudFormation template. |
| `Description` | 2–5 | A sentence CloudFormation shows next to the stack. |
| `Parameters` | 7–52 | Values you fill in at deploy time. The template stays the same; the values change. |
| `Conditions` | 54–55 | A yes/no test used to skip a resource. |
| `Resources` | 57–353 | The AWS objects to create. This is the body of the template. |
| `Outputs` | 355–378 | Values CloudFormation prints after the stack succeeds, so you do not have to hunt for them in the console. |

Each resource has a **logical ID**, a **type**, and **properties**. In this block, `FraudApiLogGroup` is the logical ID and `AWS::Logs::LogGroup` is the type:

```yaml
FraudApiLogGroup:
  Type: AWS::Logs::LogGroup
  Properties:
    LogGroupName: /fraud-detection/api
    RetentionInDays: 30
```

The logical ID is the name other parts of the template use. The type is the AWS service object. Properties are its settings.

## Functions you will see everywhere

CloudFormation adds functions that YAML itself does not have. They look like `!Ref` or `!Sub`.

- `!Ref KeyName` inserts the value of the `KeyName` parameter.
- `!Ref FraudApiInstance` inserts the instance ID of the resource with that logical ID. For most resources, `!Ref` returns the main ID.
- `!GetAtt FraudApiInstance.PublicIp` reads one attribute of a resource. `!Ref` cannot return the public IP, so the template uses `!GetAtt`.
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
| `DockerImage` | `arthurym25/deployment-fraud-system:prod` | The Docker image and tag the startup script pulls and runs. | CI publishes several tags (`dev`, `stg`, `sha-<commit>`, `prod`) and does not publish `latest`. This parameter picks which build the server runs, so you can deploy `prod` or one commit without changing the script. |
| `AlarmEmail` | empty | Email address subscribed to the alarm topic. Empty means no subscription. SNS still asks you to confirm the address before it delivers mail. | The stack watches CPU and EC2 status checks. This is who gets told when the machine is overloaded or AWS reports it as unhealthy. Leave it empty when nobody needs those emails. |

`AllowedPattern` is a regex. CloudFormation rejects the deploy if a value does not match, before it creates anything.

## Conditions

```yaml
HasAlarmEmail: !Not [!Equals [!Ref AlarmEmail, ""]]
```

This is true when `AlarmEmail` is not an empty string. `FraudApiAlarmSubscription` has `Condition: HasAlarmEmail`, so the email subscription is created only when you pass an address. The SNS topic itself is always created.

## Resources, in the order they matter

CloudFormation may create independent resources in parallel. `!Ref` and `!GetAtt` tell it when one resource must wait for another. `DependsOn` is an extra explicit wait.

### Security group

`FraudApiSecurityGroup` is the firewall for the instance. It allows inbound TCP 22 from `SSHLocation` and inbound TCP 8000 from `ApiAllowedCidr`. A security group allows return traffic for those connections, so the template does not list outbound rules. The instance can still reach Docker Hub, Secrets Manager, and CloudWatch.

### IAM role and instance profile

`FraudApiEC2Role` is the identity the instance uses when it calls AWS. The trust policy (`AssumeRolePolicyDocument`) says only the EC2 service may assume this role. That is what attaches the role to a virtual machine.

The role can do four things:

1. `AmazonSSMManagedInstanceCore` lets you open a shell through Systems Manager Session Manager, which is useful while SSH is closed.
2. `secretsmanager:GetSecretValue` on `fraud-api/dockerhub-*` in this account and region. Secrets Manager appends a random suffix to the real ARN, which is why the ARN ends in `-*`.
3. `cloudformation:SignalResource` on this stack, so the startup script can tell CloudFormation that boot finished or failed.
4. Write to the log group `FraudApiLogGroup` and to streams inside it.

`FraudApiInstanceProfile` is a wrapper EC2 requires. The instance property `IamInstanceProfile` points at the profile, and the profile points at the role.

Creating a role needs an extra confirmation on deploy: `--capabilities CAPABILITY_IAM`.

### Log group and alarms

`FraudApiLogGroup` is CloudWatch log group `/fraud-detection/api`, kept for 30 days. The container writes a stream named `fraud-api` inside it. The name is fixed, so a second stack in the same region cannot create the same log group.

`FraudApiAlarmTopic` is an SNS topic named from the stack, for example `my-stack-fraud-api-alarms`.

`FraudApiCpuAlarm` fires when average CPU is above 80% for two periods of 5 minutes. Missing data does not fire it.

`FraudApiStatusAlarm` fires when EC2 reports a failed status check twice in a row, one minute apart. Missing data fires it, because a silent instance is treated as unhealthy.

Both alarms publish to the SNS topic. Email is delivered only if you set `AlarmEmail` and confirm the subscription.

### The instance

`FraudApiInstance` is one EC2 machine:

- Amazon Linux 2023, looked up from SSM.
- Your key pair and instance type.
- A public IPv4 address on the default network interface.
- The security group attached to that interface.
- A 30 GB encrypted gp3 disk as `/dev/xvda`.
- IMDSv2 required (`HttpTokens: required`). The instance metadata service is how software on the machine gets temporary credentials for the IAM role. Version 2 is the current requirement.

`CreationPolicy` tells CloudFormation to wait until the machine reports success. The wait is 15 minutes (`PT15M`). The startup script sends that report with `cfn-signal`. If the script exits with an error, the trap sends a failure signal and the stack rolls back. If no signal arrives in 15 minutes, the stack fails the same way.

`DependsOn: FraudApiLogGroup` makes the log group exist before the instance boots, because the container is configured with `awslogs-create-group=false` and will not create the group itself.

### Startup script

`UserData` is a bash script that runs once as root on first boot. CloudFormation substitutes `${AWS::StackName}`, `${DockerHubSecretId}`, `${DockerImage}`, and the other `${...}` values before the instance sees the script. The `{{.State.Running}}` braces are left for the `docker inspect` format string; `!Sub` only replaces `${...}`.

The script does this:

1. Install Docker, `jq`, and `aws-cfn-bootstrap` (the package that provides `cfn-signal`).
2. Start Docker.
3. Install an error trap that wipes the Docker token and signals failure.
4. Turn command tracing off, then read the Docker Hub username and token from Secrets Manager. Tracing stays off so the token is not written to `/var/log/cloud-init-output.log`.
5. `docker login`, pull the image, then log out and delete the secret file and Docker config.
6. Run the container in the background, named `fraud-detection-api`, with `--restart unless-stopped`, publishing port 8000, and sending logs to the CloudWatch group.
7. Wait up to about 30 seconds for the container to be running.
8. Signal success.

`${!DOCKER_USER}` is bash indirect expansion, not CloudFormation. `!Sub` turns it into `${DOCKER_USER}` in the script that actually runs. The leading `!` tells `!Sub` to leave that dollar expression alone.

## Outputs

Outputs are the values CloudFormation prints on the stack's Outputs tab after the deploy succeeds. You use them instead of searching the console for the instance, the log group, or the dashboard. They are not inputs. Changing an output in the file does not change the running system; it only changes what gets printed next time.

| Output | What you get | Why you use it |
|---|---|---|
| `InstanceId` | The EC2 id, such as `i-0abc123`. | The console, the alarms, and the AWS CLI all refer to the machine by this id. You need it to stop the instance, open a Session Manager shell, or check why the stack is still waiting on the boot signal. |
| `PublicIp` | The public IPv4 address of that machine. | This is the address of the server on the internet. SSH uses it (`ec2-user@<public-ip>`). It can change if the instance is stopped and started again. |
| `PredictUrl` | `http://<public-ip>:8000/predict` | This is the fraud API. A client POSTs a JSON body of features here and gets a prediction back. It is the address you give to a notebook, a test script, or another service. |
| `LogGroupName` | `/fraud-detection/api` | Container stdout and stderr go here, in a stream named `fraud-api`. Open this log group in CloudWatch when a request fails or the process crashes. The group is kept for 30 days. |
| `AlarmTopicArn` | The ARN of the SNS topic, such as `arn:aws:sns:eu-central-1:511743255923:my-stack-fraud-api-alarms`. | CPU and status-check alarms publish to this topic. Use the ARN to add another subscriber later, such as a second email or a chat webhook, without editing the alarms. |
| `DashboardName` | The CloudWatch dashboard name, such as `my-stack-fraud-api`. | One page with CPU, network, status checks, and the latest API logs. Search this name in CloudWatch dashboards instead of building the graphs yourself. |

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
| ARN | Amazon Resource Name | The unique address of one AWS object, used in the IAM policy and printed for the alarm topic. Example: `arn:aws:logs:eu-central-1:123456789012:log-group:/fraud-detection/api`. |
| AWS | Amazon Web Services | The cloud these resources run in: the machine, the logs, the alarms, and the secret. |
| CIDR | Classless Inter-Domain Routing | A way to write a range of IP addresses. `203.0.113.4/32` is one address. `0.0.0.0/0` is every address. `SSHLocation` and `ApiAllowedCidr` use this form. |
| CI | Continuous integration | The pipeline that builds and publishes the Docker image tags `dev`, `stg`, `sha-<commit>`, and `prod`. |
| CLI | Command-line interface | The `aws` command used on your laptop and inside the startup script. |
| CPU | Central processing unit | The compute the instance spends. The CPU alarm fires when average use stays above 80%. |
| EBS | Elastic Block Store | The virtual hard disk. This instance has one 30 GB encrypted `gp3` volume. |
| EC2 | Elastic Compute Cloud | AWS virtual machines. `FraudApiInstance` is one EC2 instance. |
| GB | Gigabyte | Size of that disk: 30 GB. |
| HTTP | Hypertext Transfer Protocol | The protocol a client uses to call `http://<public-ip>:8000/predict`. This stack does not put HTTPS in front of it. |
| IAM | Identity and Access Management | Users, roles, and permissions. The instance gets a role so it can read the Docker Hub secret, write logs, and signal the stack. |
| ID | Identifier | The short name AWS assigns, such as the instance id `i-0abc123`. |
| IMDS | Instance Metadata Service | A link from software on the machine to EC2, at `169.254.169.254`. The instance uses it to fetch temporary credentials for its IAM role. |
| IMDSv2 | Instance Metadata Service version 2 | The current IMDS. `HttpTokens: required` means the instance refuses the older version. |
| IP | Internet Protocol address | The numeric address of a machine. `PublicIp` is the one clients and SSH use. |
| IPv4 | Internet Protocol version 4 | The familiar address form, such as `203.0.113.4`. This template assigns a public IPv4 address. |
| JSON | JavaScript Object Notation | The text format of the Docker Hub secret (`username` and `token`) and of the CloudWatch dashboard body. |
| KMS | Key Management Service | AWS encryption keys. The parameter note means the secret uses the default Secrets Manager key, so the role needs no extra KMS permission. |
| RAM | Random-access memory | Memory on the instance. `InstanceType` chooses how much CPU and RAM the model gets. |
| SNS | Simple Notification Service | The alarm mailbox. CPU and status alarms publish a message to an SNS topic, and email is one possible subscriber. |
| SSH | Secure Shell | A remote terminal on port 22. `KeyName` is the key, `SSHLocation` is who may connect. |
| SSM | AWS Systems Manager | The service that stores the current Amazon Linux AMI id, and Session Manager, which can open a shell while SSH is closed. |
| TCP | Transmission Control Protocol | The network protocol of the two firewall rules: port 22 for SSH and port 8000 for the API. |
| URL | Uniform Resource Locator | A web address. `PredictUrl` is the one clients call. |
| YAML | YAML Ain't Markup Language | The file format of `fraud-api.yaml`. Indentation marks what belongs inside what. |
