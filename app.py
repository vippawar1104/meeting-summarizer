from flask import Flask, redirect, url_for

app = Flask(__name__)

@app.route('/')
def home():
    # Redirect to the Streamlit app
    # For local development, redirect to localhost:8501
    # For production, replace with your deployed Streamlit URL
    return redirect("http://localhost:8501")

if __name__ == '__main__':
    app.run(debug=True)
