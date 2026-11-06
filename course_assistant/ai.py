from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    u = urllib.parse.urlsplit(value)
    if not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("AI 接口地址必须是完整基础地址，不能包含用户名、密码、查询参数或片段。")
    if u.scheme != "https" and not (u.scheme == "http" and u.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("远程 AI 接口必须使用 HTTPS；本机接口可使用 HTTP。")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def complete(settings, key: str, prompt: str, *, test=False):
    base = validate_base_url(settings["ai_base_url"])
    model = str(settings.get("ai_model", "")).strip()
    if not key or not model:
        raise ValueError("请先保存 API 密钥和模型名称。")
    if any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("密钥格式异常，请重新复制完整密钥，勿包含换行或空格。")
    if key.startswith("key_"):
        raise ValueError("这看起来是密钥 ID，请填写创建时显示的完整 Secret Key。")
    if settings["ai_protocol"] == "responses":
        endpoint = "/responses"
        body = {"model": model, "input": prompt, "max_output_tokens": 1000 if test else 1800, "store": False}
    else:
        endpoint = "/chat/completions"
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 128 if test else 1200}
    req = urllib.request.Request(base + endpoint, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("AI 返回过大，请缩短材料后重试。")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        messages = {401: "认证失败，请检查完整密钥及所属平台。", 403: "上游拒绝访问，请检查账号权限。",
                    429: "上游额度不足或请求受限，请查看供应商后台。"}
        raise ValueError(f"AI 接口 HTTP {e.code}：" + messages.get(e.code, "请求未成功，请检查接口配置。")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("AI 接口连接失败或超时；未自动重试，避免重复计费。") from None
    except json.JSONDecodeError:
        raise ValueError("接口未返回有效 JSON，请核对基础地址和协议。") from None
    except (ValueError, UnicodeError):
        raise ValueError("AI 请求格式无效，请检查地址、密钥与模型设置。") from None
    if settings["ai_protocol"] == "responses":
        text = "\n".join(c.get("text", "") for o in data.get("output", [])
                         if o.get("type") == "message" for c in o.get("content", []) if c.get("type") == "output_text")
    else:
        choices = data.get("choices") or []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("AI 没有返回可用文本；本次不能算测试或生成成功。")
    return text.strip()
