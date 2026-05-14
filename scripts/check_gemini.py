import google.generativeai as genai
import os

def check_gemini_3():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("GOOGLE_API_KEY not found in environment.")
        return
    
    genai.configure(api_key=api_key)
    try:
        models = genai.list_models()
        print("Available models:")
        found = False
        for m in models:
            print(f" - {m.name}")
            if "gemini-3-flash" in m.name:
                found = True
        
        if found:
            print("\nSUCCESS: Gemini 3 Flash is available!")
        else:
            print("\nNOT FOUND: Gemini 3 Flash is not in the list of available models for this key.")
    except Exception as e:
        print(f"Error checking models: {e}")

if __name__ == "__main__":
    check_gemini_3()
