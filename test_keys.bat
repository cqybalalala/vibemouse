@echo off
.venv\Scripts\python -c "
import time
from pynput.keyboard import Listener

print('=== Press any key in 10 seconds ===')
count = [0]

def on_press(key):
    count[0] += 1
    print('Key:', key)

listener = Listener(on_press=on_press)
listener.start()
for i in range(10):
    time.sleep(1)
    print(str(i+1) + 's, events: ' + str(count[0]))
listener.stop()
print('Total:', count[0])
"
pause
