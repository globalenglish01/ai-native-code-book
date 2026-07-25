"""让`examples/tests/`下的测试能够`from products.xxx import ...`。

`examples/`本身不是workspace的一个包（它是示例代码，不随框架发布），
所以需要在conftest里手动把`examples/`加进sys.path，而不是给每个产品
示例都建一个可安装的包结构。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
