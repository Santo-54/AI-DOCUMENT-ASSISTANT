
import os
from dotenv import load_dotenv
from mistralai.client import Mistral

# Load API key from .env
load_dotenv()
api_key = os.getenv("MISTRAL_API_KEY")

if not api_key:
    raise ValueError("MISTRAL_API_KEY missing in .env file")

# Initialize Mistral client
client = Mistral(api_key=api_key)

# System prompt
system_prompt = """
You are a Developer Documentation AI Assistant.

Your responsibilities:
1. Explain APIs and technical documentation clearly.
2. Help developers understand errors and debug code.
3. Provide Python code examples when useful.
4. Use simple, structured explanations.
5. Never invent information about private project documents.
6. If documentation is not provided, clearly say so.

For technical questions, use this format when appropriate:
- Explanation
- Example
- Important notes
"""

# Conversation history
messages = [
    {"role": "system", "content": system_prompt}
]

print("Developer Documentation AI Assistant")
print("Type 'exit' to quit.\n")

while True:
    question = input("You: ").strip()

    if question.lower() == "exit":
        print("Chatbot: Bye da!")
        break

    if not question:
        continue

    messages.append({
        "role": "user",
        "content": question
    })

    try:
        response = client.chat.complete(
            model="ministral-8b-2512",
            messages=messages
        )

        answer = response.choices[0].message.content

        if not isinstance(answer, str):
            answer = str(answer)

        messages.append({
            "role": "assistant",
            "content": answer
        })

        print(f"\nAssistant: {answer}\n")

    except Exception as error:
        # Remove the unanswered user message so history stays consistent
        messages.pop()
        print(f"\nError: {error}\n")
