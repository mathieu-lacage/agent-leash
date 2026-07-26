Test that aleash run passes output through correctly.

  $ aleash run echo hello | tr -d '\r'
  hello

Test that the sandbox propagates exit code.

  $ aleash run false; echo "exit:$?"
  exit:1
