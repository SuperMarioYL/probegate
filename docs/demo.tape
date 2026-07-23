Output assets/demo.gif
Set FontSize 14
Set Width 1100
Set Height 560
Set Theme "Dracula"
Set Padding 20

Type "probegate --help"
Enter
Sleep 1200ms

Type "probegate demo --model deepseek-coder --probe compile"
Enter
Sleep 3500ms

Type "probegate gate --demo --probe compile --threshold 0.5"
Enter
Sleep 3000ms

Type "probegate gate --demo --probe lint"
Enter
Sleep 2500ms
