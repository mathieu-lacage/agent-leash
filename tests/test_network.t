Block test — proxy kills the flow, no internet needed.

  $ printf '{"domains":{"example.com":"block"}}\n' > .aleash-network-permissions.json
  $ aleash run curl -sf http://example.com/; echo "exit:$?"
  exit:* (glob)

Allow test — proxy forwards, requires internet.

  $ printf '{"domains":{"example.com":"allow"}}\n' > .aleash-network-permissions.json
  $ aleash run curl -sf http://example.com/ > /dev/null; echo "exit:$?"
  exit:0
