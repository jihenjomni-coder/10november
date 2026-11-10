# Roamly tourism bot

Roamly is a small, server-backed tourism chat app that uses an Azure OpenAI chat
completions deployment. The Azure API key stays on the server and is not sent to
the browser.

## Run locally

1. Make sure a `.env` file (or legacy `.ENV.txt`) contains
   `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`,
   `AZURE_OPENAI_API_VERSION`, and `AZURE_OPENAI_API_KEY`.
2. Run `python server.py` from this folder.
3. Open <http://localhost:8000>.

The app uses only the Python standard library. Set the `PORT` environment
variable to use a different local port.
