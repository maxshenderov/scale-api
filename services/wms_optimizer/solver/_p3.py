import sys  
fn=r'hybrid_v7.py'  
c=open(fn,'r',encoding='utf-8').read()  
old='    # ------------------------------------------------------------------  
    # Фаза 3: Joint CP-SAT Repack  
    # ------------------------------------------------------------------'  
if old in c:  
  start=c.index(old)  
  ns=c.index('    # ------------------------------------------------------------------  
    # Операции  
    # ------------------------------------------------------------------',start)  
  new=open(r'_new_phase.txt','r',encoding='utf-8').read()  
  c=c[:start]+new+c[ns:]  
  open(fn,'w',encoding='utf-8').write(c)  
  print('OK')  
else:  
  print('NOT FOUND')  
