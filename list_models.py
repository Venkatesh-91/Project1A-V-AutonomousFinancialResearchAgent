from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.environ["GROQ_API_KEY"])

models = client.models.list()
print("Models available to your account:\n")
for m in models.data:
    print(f"  {m.id}")
    