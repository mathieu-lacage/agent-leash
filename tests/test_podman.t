Skip if podman is not installed on the host.

  $ which podman > /dev/null 2>&1 || exit 80

Without the podman service, podman is unavailable inside the sandbox.

  $ aleash run podman ps </dev/null >/dev/null 2>&1
  [1]

Enable the podman service.

  $ printf '{"services":{"podman":{"enabled":true}}}\n' > .aleash-services.json

Skip if the host podman socket cannot be started.

  $ systemctl --user start podman.socket 2>/dev/null || exit 80

With the podman service enabled, podman reaches the host daemon.

  $ aleash run podman ps

hello </dev/null >/dev/null 2>&1
