"""
This module implements a Flask application that serves a machine learning model for predictions.
The application loads a pre-trained linear regression model and provides an endpoint to make predictions
based on input data sent via POST requests.

The model is expected to be saved in a file named "linear_model.pkl".
"""

from flask import Flask, request, jsonify  
import joblib  
import numpy as np 
import os

# Initialize the Flask application
app = Flask(__name__)

# Load the trained model saved in the file "linear_model.pkl"
model = joblib.load("linear_model.pkl")

@app.route("/predict", methods=["POST"])
def predict():
    """
    Handle POST requests to the "/predict" endpoint.

    This function receives input data in JSON format, processes it, and uses the loaded
    machine learning model to make predictions. The predictions are returned in JSON format.

    Returns:
        Response: A JSON response containing the predictions or an error message.
    """
    try:
        # Get the data sent in the request in JSON format
        data = request.get_json()

        # Convert the received data into a NumPy array and format it as a column matrix
        X_new = np.array(data["X"]).reshape(-1, 1)

        # Make predictions using the loaded model and convert the result to a list
        prediction = model.predict(X_new).tolist()

        # Return the prediction in JSON format
        return jsonify({"prediction": prediction})
    
    except Exception as e:
        # Return an error if there is an issue processing the request
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    """
    Start the Flask server.

    The server listens on the port specified in the "PORT" environment variable (default: 5000)
    and binds to all available network interfaces (host: "0.0.0.0").
    """
    port = int(os.environ.get("PORT", 5000))  # Get the port from the environment in Heroku
    app.run(host="0.0.0.0", port=port)
