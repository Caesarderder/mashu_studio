#!/usr/bin/env python3
"""Plan or apply six portable Caesar skills; refuse unreviewed local changes."""
from pathlib import Path
import argparse,hashlib,json,shutil,tempfile
NAMES=('caesar-docs','caesar-builda','caesar-design','caesar-art','caesar-program','caesar-producer')
def digest(root):
 return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store'}
def main():
 a=argparse.ArgumentParser(description=__doc__);a.add_argument('--source',type=Path,required=True);a.add_argument('--project',type=Path,default=Path.cwd());a.add_argument('--apply',action='store_true');args=a.parse_args()
 root=args.project.resolve();source=args.source.resolve();lockfile=root/'.agents/skills-lock.json'
 lock=json.loads(lockfile.read_text()) if lockfile.exists() else {'version':1,'skills':{}}
 plan=[]
 for name in NAMES:
  incoming=source/name;target=root/'.agents/skills'/name
  if not (incoming/'SKILL.md').is_file():raise SystemExit('Missing source skill: '+name)
  expected=lock['skills'].get(name,{}).get('files');current=digest(target);wanted=digest(incoming)
  if current and current!=wanted and (expected is None or current!=expected):raise SystemExit('Local changes require review: '+str(target))
  plan.append((name,incoming,target,wanted,current!=wanted))
 print(json.dumps({'project':str(root),'apply':args.apply,'changes':[n for n,_,_,_,changed in plan if changed]},ensure_ascii=False))
 if not args.apply:return
 for name,incoming,target,wanted,changed in plan:
  if changed:
   target.parent.mkdir(parents=True,exist_ok=True)
   with tempfile.TemporaryDirectory(prefix='caesar-sync-') as tmp:
    ready=Path(tmp)/name;shutil.copytree(incoming,ready,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
    if target.exists():shutil.rmtree(target)
    shutil.copytree(ready,target)
  lock['skills'][name]={'category':'docs' if name=='caesar-docs' else 'builda' if name=='caesar-builda' else 'caesar-game','index':'docs/indexes/'+name.removeprefix('caesar-')+'.md','version':(incoming/'VERSION').read_text().strip(),'source':'portable-caesar-suite','files':wanted}
 lock['version']=1;lockfile.parent.mkdir(parents=True,exist_ok=True);lockfile.write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
