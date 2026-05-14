from google import genai
import os

def list_models():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        # Check if we can use ADC or if there's a default way
        print("GOOGLE_API_KEY not found. Attempting to use default credentials...")
    
    try:
        client = genai.Client(api_key=api_key)
        # In newer SDKs, it might be client.models.list()
        print("Fetching models...")
        models = client.models.list()
        for model in models:
            print(f" - {model.name}")
    except Exception as e:
        print(f"Failed to list models: {e}")

if __name__ == "__main__":
    list_models()
