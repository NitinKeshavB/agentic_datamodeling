"""Quick smoke-test for the Databricks Model Serving endpoint."""

import os

from openai import OpenAI

DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
DATABRICKS_HOST = os.environ.get(
    "DATABRICKS_HOST", "https://adb-3328600036097005.5.azuredatabricks.net"
)
SERVING_ENDPOINT = os.environ.get("SERVING_ENDPOINT", "databricks-claude-opus-4-8")

client = OpenAI(
    api_key=DATABRICKS_TOKEN,
    base_url=f"{DATABRICKS_HOST}/serving-endpoints",
)

response = client.chat.completions.create(
    model=SERVING_ENDPOINT,
    messages=[{"role": "user", "content": "Say hello in one word."}],
    max_tokens=10,
)

print("Response:", response.choices[0].message.content)
print("Model:", response.model)
