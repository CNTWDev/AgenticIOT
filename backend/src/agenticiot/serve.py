"""Single-process launcher with drain-before-transport-close semantics."""

import argparse
import logging

import uvicorn

from agenticiot.main import create_app


class DrainingServer(uvicorn.Server):
    def handle_exit(self, sig, frame):
        app = self.config.app
        if hasattr(app.state, "node_hub"):
            app.state.node_hub.draining = True
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets=None):
        app = self.config.app
        if hasattr(app.state, "node_hub"):
            await app.state.node_hub.close()
        await super().shutdown(sockets)


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    server = DrainingServer(
        uvicorn.Config(
            create_app(),
            host=args.host,
            port=args.port,
            workers=1,
            ws="websockets-sansio",
            ws_max_size=262144,
            ws_max_queue=16,
            timeout_graceful_shutdown=10,
        )
    )
    server.run()


if __name__ == "__main__":
    main()
