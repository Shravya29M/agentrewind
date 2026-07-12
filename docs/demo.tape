# vhs docs/demo.tape  (regenerates docs/demo.gif)
Output docs/demo.gif
Set Shell bash
Set FontSize 15
Set Width 980
Set Height 640
Set Padding 12
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "export PATH=$HOME/.venvs/agentlens/bin:$PATH AGENTLENS_DB=/tmp/agentlens-demo-gif.db && rm -f /tmp/agentlens-demo-gif.db* && clear"
Enter
Show

Type "# two runs of the same agent — v2 has a one-word prompt change"
Enter
Type "python examples/research_agent.py"
Enter
Sleep 2.5

Type "agentlens list"
Enter
Sleep 2

Hide
Type `ids=$(agentlens list | awk 'NR>1{print $1}' | tail -2 | tr '\n' ' ') && clear`
Enter
Show

Type "agentlens diff $ids"
Enter
Sleep 6
