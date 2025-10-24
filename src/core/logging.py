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
