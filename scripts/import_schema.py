"""旧 schema 导入入口（兼容包装）。

新代码请直接运行 ``python -m scripts.migrate_account_billing``。保留本文件只为避免
旧部署命令静默失效；写操作仍要求显式设置 ``ALLOW_SCHEMA_IMPORT=true``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrate_account_billing import main  # noqa: E402

if __name__ == "__main__":
    if os.environ.get("ALLOW_SCHEMA_IMPORT", "").lower() != "true":
        raise SystemExit(
            "拒绝执行：请改用 python -m scripts.migrate_account_billing；兼容入口需设置 ALLOW_SCHEMA_IMPORT=true。"
        )
    raise SystemExit(main())
