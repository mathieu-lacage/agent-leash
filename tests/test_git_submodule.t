Regression test for https://github.com/mathieu-lacage/agent-leash/issues/1 —
a sandboxed agent cwd'd into a git submodule must be able to write (commit)
since the submodule's real gitdir lives outside cwd and used to be bound
read-only.

Set up a parent repo with a submodule checked out inside it.

  $ git init -q sub
  $ git -C sub commit -q --allow-empty -m init
  $ git init -q super
  $ git -c protocol.file.allow=always -C super submodule add -q ../sub sub

Commit from inside the sandbox, with cwd rooted at the submodule.

  $ cd super/sub
  $ aleash run git -c user.name=test -c user.email=test@test.com commit -q --allow-empty -m "sandboxed commit" </dev/null >/dev/null; echo "exit:$?"
  exit:0

The commit is visible from the host, proving it was a real write.

  $ git log --oneline -1
  * sandboxed commit (glob)
