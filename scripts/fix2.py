lines = open('modules/auth.py', 'r', encoding='utf-8').readlines()
# Fix indentation - remove leading spaces from lines 82-85
lines[82] = 'warnings.warn(\n'
lines[83] = '    "JZTZ_BOOTSTRAP_ADMIN_PASSWORD not set; using default admin password admin123"\n'
lines[84] = '    "Set JZTZ_BOOTSTRAP_ADMIN_PASSWORD env var in production to override"\n'
lines[85] = ')\n'
open('modules/auth.py', 'w', encoding='utf-8').write(''.join(lines))
print('Fixed')

# Also check config.py 
lines = open('modules/config.py', 'r', encoding='utf-8').readlines()
for i, l in enumerate(lines):
    if 'warnings.warn' in l and 'APP_SECRET_KEY' in ''.join(lines[i:i+3]):
        lines[i] = 'warnings.warn(\n'
        lines[i+1] = '    "APP_SECRET_KEY env var not set; using insecure default change-me-in-production"\n'
        lines[i+2] = ')\n'
        break
open('modules/config.py', 'w', encoding='utf-8').write(''.join(lines))
print('Fixed config indentation')

import py_compile
for mod in ['modules/auth.py', 'modules/config.py', 'routes/health.py', 'routes/api.py']:
    py_compile.compile(mod, doraise=True)
print('All compile OK')
