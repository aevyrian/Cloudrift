import os

import uvicorn

from .main import make_app


if __name__ == "__main__":
    # 媒体元素使用查询密钥，不让访问日志记录其完整 URL。
    uvicorn.run(make_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), access_log=False)
