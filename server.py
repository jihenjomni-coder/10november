import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
ENV_FILES = (ROOT / ".ENV.txt", ROOT / ".env")
PORT = int(os.environ.get("PORT", "8000"))
SYSTEM_PROMPT = (
    "You are Roamly, a friendly and practical tourism guide. Help travelers with "
    "destinations, local attractions, food, culture, transport, itineraries, and "
    "travel planning. Ask concise follow-up questions when destination, dates, "
    "budget, or interests would materially improve your recommendations. Be clear "
    "when details such as opening hours, prices, visa rules, or safety conditions "
    "may change, and advise checking official current sources. Never invent "
    "specific real-time information."
)


def _normalize_env_value(key, value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if key == "AZURE_OPENAI_API_VERSION" and value.lower().startswith("v:"):
        value = value[2:].strip()
    return value


def _load_env_file(env_path):
    if not env_path.exists():
        return {}
    loaded = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", stripped)
        if not match:
            continue
        key, value = match.groups()
        loaded[key] = _normalize_env_value(key, value)
    return loaded


def load_config():
    env_values = {}
    for env_path in ENV_FILES:
        env_values.update(_load_env_file(env_path))
    for key, value in env_values.items():
        if not os.environ.get(key):
            os.environ[key] = value

    deployment_name = os.environ.get("deployment_name") or env_values.get("deployment_name")
    if deployment_name and not os.environ.get("AZURE_OPENAI_DEPLOYMENT"):
        os.environ["AZURE_OPENAI_DEPLOYMENT"] = deployment_name
    raw_deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    if raw_deployment.lower().startswith("deployed ") and deployment_name:
        os.environ["AZURE_OPENAI_DEPLOYMENT"] = deployment_name

    required = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_API_KEY",
    )
    config = {}
    for name in required:
        value = os.environ.get(name, "").strip()
        if name == "AZURE_OPENAI_API_VERSION" and value.lower().startswith("v:"):
            value = value[2:].strip()
        if name == "AZURE_OPENAI_DEPLOYMENT" and value.lower().startswith("deployed ") and deployment_name:
            value = deployment_name
        config[name] = value

    missing = [name for name in required if not config[name]]
    if missing:
        raise RuntimeError("Missing required Azure settings: " + ", ".join(missing))
    return config


class TourismBotHandler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            body = (ROOT / "index.html").read_bytes()
        except OSError:
            self.send_error(500, "The tourism bot page could not be loaded.")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/chat":
            self._send_json(404, {"error": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                self._send_json(400, {"error": "Send a message under 64 KB."})
                return
            payload = json.loads(self.rfile.read(length))
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                self._send_json(400, {"error": "Please send a message to get started."})
                return
            cleaned_messages = []
            for item in messages[-20:]:
                if (
                    not isinstance(item, dict)
                    or item.get("role") not in ("user", "assistant")
                    or not isinstance(item.get("content"), str)
                ):
                    self._send_json(400, {"error": "The chat history is invalid."})
                    return
                cleaned_messages.append(
                    {"role": item["role"], "content": item["content"][:8000]}
                )
            config = load_config()
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "The request was not valid JSON."})
            return
        except RuntimeError as error:
            self._send_json(503, {"error": str(error)})
            return
        except OSError:
            self._send_json(500, {"error": "Could not read the Azure settings file."})
            return

        endpoint = config["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        deployment = config["AZURE_OPENAI_DEPLOYMENT"]
        api_version = config["AZURE_OPENAI_API_VERSION"]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *cleaned_messages,
        ]
        if urlsplit(endpoint).path.rstrip("/").endswith("/openai/v1"):
            url = f"{endpoint}/chat/completions"
            request_payload = {
                "model": deployment,
                "messages": messages,
                "max_completion_tokens": 2000,
            }
            if deployment.lower().startswith("gpt-5"):
                request_payload["reasoning_effort"] = "low"
        else:
            url = (
                f"{endpoint}/openai/deployments/{quote(deployment, safe='')}"
                f"/chat/completions?{urlencode({'api-version': api_version})}"
            )
            request_payload = {
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 700,
            }
        request_body = json.dumps(request_payload).encode("utf-8")
        request = Request(
            url,
            data=request_body,
            headers={
                "Content-Type": "application/json",
                "api-key": config["AZURE_OPENAI_API_KEY"],
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            reply = result["choices"][0]["message"]["content"]
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("The Azure response did not contain a reply.")
            self._send_json(200, {"reply": reply})
        except HTTPError as error:
            print(f"Azure OpenAI returned HTTP {error.code}.")
            self._send_json(
                502,
                {"error": f"Azure OpenAI returned HTTP {error.code}. Check the endpoint, deployment name, and API version in .env (or .ENV.txt)."},
            )
        except (URLError, TimeoutError):
            self._send_json(502, {"error": "Could not reach Azure OpenAI. Check the endpoint and network connection."})
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            self._send_json(502, {"error": "Azure OpenAI returned an unexpected response."})

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))


if __name__ == "__main__":
    print(f"Tourism bot is ready at http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), TourismBotHandler).serve_forever()
