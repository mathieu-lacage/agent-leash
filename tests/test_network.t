Localhost HTTP — service started inside the sandbox reachable at localhost and 127.0.0.1.

  $ aleash run bash -c 'python3 -m http.server 8000 >/dev/null 2>&1 & sleep 1 && curl -sf http://localhost:8000/ >/dev/null && curl -sf http://127.0.0.1:8000/ >/dev/null' >/dev/null; echo "exit:$?"
  exit:0

Block test — proxy kills the flow, no internet needed.

  $ printf '{"domains":{"example.com":"block"}}\n' > .aleash-network-permissions.json
  $ aleash run curl -sf http://example.com/; echo "exit:$?"
  exit:* (glob)

Allow test — proxy forwards, requires internet.

  $ printf '{"domains":{"example.com":"allow"}}\n' > .aleash-network-permissions.json
  $ aleash run curl -sf http://example.com/ > /dev/null; echo "exit:$?"
  exit:0
