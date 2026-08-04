import sys

argv = sys.argv[1:]
if "--serve" in argv:
    from .web import serve
    argv.remove("--serve")
    port = None
    if "--port" in argv:
        i = argv.index("--port")
        port = int(argv[i + 1])
        del argv[i:i + 2]
    sys.exit(serve(port, open_browser="--no-open" not in argv))

from .cli import main
sys.exit(main())
