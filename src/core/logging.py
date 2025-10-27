# import logging
# import json
# import sys

# def configure_json_logging():
#     class JsonFormatter(logging.Formatter):
#         def format(self, record):
#             d = {
#                 "lvl": record.levelname,
#                 "msg": record.getMessage(),
#                 "logger": record.name,
#                 "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
#             }
#             if record.exc_info:
#                 d["exc"] = self.formatException(record.exc_info)
#             return json.dumps(d, ensure_ascii=False)
#     h = logging.StreamHandler(sys.stdout)
#     h.setFormatter(JsonFormatter())
#     root = logging.getLogger()
#     root.handlers = [h]
#     root.setLevel(logging.INFO)

import logging
import json
import sys

def configure_json_logging():
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            d = {
                "lvl": record.levelname,
                "msg": record.getMessage(),
                "logger": record.name,
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            }
            if record.exc_info:
                d["exc"] = self.formatException(record.exc_info)
            return json.dumps(d, ensure_ascii=False)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(logging.INFO)
    
    # 🔇 ДОБАВЛЕНО: Отключаем шумные INFO логи (WARNING и ERROR всё равно пишутся!)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)      # "Update id=X is handled"
    logging.getLogger("httpx").setLevel(logging.WARNING)              # "HTTP Request: GET/POST"
    logging.getLogger("httpcore").setLevel(logging.WARNING)           # HTTP core logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)     # "POST /tg/webhook HTTP/1.1 200"
    logging.getLogger("hpack").setLevel(logging.WARNING)              # HTTP/2 logs