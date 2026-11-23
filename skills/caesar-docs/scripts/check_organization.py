#!/usr/bin/env python3
"""Read-only audit of six skill/index routes, local copies and official ownership."""
from pathlib import Path
import argparse,hashlib,json,re,sys
NAMES=('caesar-docs','caesar-builda','caesar-design','caesar-art','caesar-program','caesar-producer')
def audit(root):
 errors=[];lockfile=root/'.agents/skills-lock.json'
 if not lockfile.is_file():return ['Missing .agents/skills-lock.json']
 lock=json.loads(lockfile.read_text())
 for name in NAMES:
  d=root/'.agents/skills'/name;idx=root/'docs/indexes'/(name.removeprefix('caesar-')+'.md')
  if not (d/'SKILL.md').is_file():errors.append('Missing skill '+name);continue
  if not idx.is_file():errors.append('Missing index '+str(idx));continue
  if 'docs/indexes/'+idx.name not in (d/'SKILL.md').read_text():errors.append('Missing skill to index route '+name)
  if name not in idx.read_text():errors.append('Missing index to skill route '+name)
  expected=lock.get('skills',{}).get(name,{}).get('files',{})
  current={str(p.relative_to(d)):hashlib.sha256(p.read_bytes()).hexdigest() for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store'}
  if current!=expected:errors.append('Skill content drift '+name)
  for p in d.rglob('SKILL.md'):
   if p!=d/'SKILL.md':errors.append('Nested discoverable skill '+str(p))
  for p in d.rglob('*.md'):
   prose=re.sub(r'```.*?```|`[^`\n]+`','',p.read_text(),flags=re.S)
   for target in re.findall(r'\[[^]]*\]\(([^)]+)\)',prose):
    target=target.split('#',1)[0].strip()
    if not target or re.match(r'\w+://|mailto:',target) or '<' in target:continue
    if not (p.parent/target).exists():errors.append(str(p.relative_to(root))+': missing '+target)
 for p in (root/'.agents/skills').glob('*/SKILL.md'):
  if p.parent.name not in NAMES and p.parent.name not in ['builda-online','builda-online-diagnostics']:errors.append('Unexpected active project skill '+p.parent.name)
 return errors
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,default=Path.cwd());a=p.parse_args();errors=audit(a.project.resolve());print(json.dumps({'project':str(a.project.resolve()),'valid':not errors,'errors':errors},ensure_ascii=False,indent=2));sys.exit(bool(errors))
