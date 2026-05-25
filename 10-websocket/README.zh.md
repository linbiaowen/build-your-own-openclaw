# 步骤 10：WebSocket

> 想要以编程方式与智能体交互？

## 前置条件

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
# 取消注释 api 部分
```

## 这节做什么

开个 WebSocket 接口，方便程序调用。

<img src="10-websocket.svg" align="center" width="100%" />

## 关键组件

- **WebSocketWorker** - 管理 WebSocket 连接并广播事件
- **WebSocket Handle** - 具有 WebSocket 端点的 Web 服务器

[src/mybot/server/websocket_worker.py](src/mybot/server/websocket_worker.py)

```python
class WebSocketWorker(SubscriberWorker):
    """Manages WebSocket connections and event broadcasting."""

    def __init__(self, context: "SharedContext"):
        self.clients: Set[WebSocket] = set()

        # Auto-subscribe to event classes
        for event_class in [InboundEvent, OutboundEvent]:
            self.context.eventbus.subscribe(event_class, self.handle_event)

    async def handle_connection(self, ws: WebSocket) -> None:
        self.clients.add(ws)
        try:
            await self._run_client_loop(ws)
        finally:
            self.clients.discard(ws)

    async def handle_event(self, event: Event) -> None:
        event_dict = {"type": event.__class__.__name__}
        event_dict.update(dataclasses.asdict(event))

        for client in list(self.clients):
            try:
                await client.send_json(event_dict)
            except Exception:
                self.clients.discard(client)
```

[src/mybot/server/app.py](src/mybot/server/app.py)

```python
def create_app(context: SharedContext) -> FastAPI:
    app = FastAPI(title="MyBot WebSocket Server")
    # ... wiring

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        if context.websocket_worker is None:
            await websocket.close(code=1013, reason="WebSocket not available")
            return
        await context.websocket_worker.handle_connection(websocket)

    return app
```

## 试一试

```bash
cd 10-websocket
uv run my-bot server

# INFO:     Application startup complete.
# INFO:     Uvicorn running on http://127.0.0.1:8031 (Press CTRL+C to quit)
```

在另一个终端中

``` bash
wscat -c ws://localhost:8031/ws
> {"source": "test", "content": "Hello, Pickle!"}
< {"type":"InboundEvent","session_id":"c8419b2b-fc20-49a6-8fd7-79a00eeb71c5","source":"platform-ws:test","content":"Hello, Pickle!","timestamp":1773369408.214437,"retry_count":0}
< {"type":"OutboundEvent","session_id":"c8419b2b-fc20-49a6-8fd7-79a00eeb71c5","source":"agent:pickle","content":"*waves paws excitedly* Hello there! 🐱\n\nI'm Pickle, your friendly cat assistant!","timestamp":1773369422.7538216,"error":null}
>
```

## 下一步

[步骤 11：多智能体路由](../11-multi-agent-routing/) - 将消息路由到专门的智能体。
