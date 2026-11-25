# 自动化测试

先按仓库 README 启动服务（`python app.py`），再在仓库根目录运行：

```bash
python -m pip install pytest requests
python -m pytest -q
```

测试通过 `requests` 对 `http://127.0.0.1:8000` 发真实 HTTP 请求。每个用例前后都会调用 `POST /_test/reset` 清空服务内存数据，可重复运行。若服务地址不同，可设置 `HOMEGUARD_BASE_URL`，例如 `HOMEGUARD_BASE_URL=http://localhost:8000 python -m pytest -q`。当前被测实现含有已记录缺陷，因此部分断言预期会失败；具体见 `docs/bugs.md`。
