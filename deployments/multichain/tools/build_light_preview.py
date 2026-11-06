"""Build an isolated HTML style study. Does not alter the real product."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'design/previews/vela-light'
OUT=D/'html';OUT.mkdir(exist_ok=True)
common=(D/'preview-common.css').read_text();js=(D/'preview.js').read_text()
links=[('index.html','Home'),('lending.html','Lending'),('trading.html','Trading'),('settings.html','Settings')]
def bar(page):
 return '<aside id="design-preview-bar"><span><i></i><strong>LIGHT PREVIEW</strong> · Example balances · No wallet connection</span><nav aria-label="Preview pages">'+''.join('<a href="'+p+'"'+(' aria-current="page"' if p==page else '')+'>'+label+'</a>' for p,label in links)+'</nav></aside>'
extras='<div id="preview-toast" role="status" hidden></div><div id="preview-profile" hidden><a href="settings.html">Profile &amp; settings →</a></div><dialog id="preview-dialog"><h2>Design preview</h2><p>This is a visual preview with example balances. No wallet request or transaction is sent.</p><button type="button">Back to preview</button></dialog>'
def rewrite(s):
 for a,b in [('https://defivela.xyz/app/#trade','trading.html'),('https://defivela.xyz/app/trading','trading.html'),('https://defivela.xyz/app/','lending.html'),('https://defivela.xyz/','index.html'),('href="/app/trading"','href="trading.html"'),('href="/app/"','href="lending.html"'),('href="/settings"','href="settings.html"'),('href="/"','href="index.html"')]:s=s.replace(a,b)
 return s
home=(ROOT/'design/vela-home.html').read_text()
style='<style>'+common+'\n'+(D/'home-light.css').read_text()+'</style>'
home=home.replace('<meta name="theme-color" content="#0d0e0b">','<meta name="theme-color" content="#faf8f2">').replace('</head>',style+'</head>').replace('<body>','<body>'+bar('index.html')).replace('</body>',extras+'<script>'+js+'</script></body>')
home=home.replace('<title>Vela — Simple borrowing on DogeOS</title>','<title>Vela — Light design preview</title>')
home=rewrite(home)
(OUT/'index.html').write_text(home)
css='\n'.join(p.read_text() for p in sorted((ROOT/'site/vela-dashboard/dist-testnet/assets').glob('*.css')))
css=re.sub(r'@import\s+[^;]+;','',css)
for name in ['lending','trading','settings']:
 fragment=(D/(name+'.fragment.html')).read_text()
 fragment=fragment.replace('0x1111… Disconnect','Preview wallet')
 # Captured controls are inert HTML; explicit local preview handlers replace React.
 fragment=re.sub(r'<script\b[^>]*>.*?</script>','',fragment,flags=re.S)
 if name=='settings':
  buttons=re.findall(r'<button\b[^>]*aria-pressed="(?:true|false)"[^>]*>.*?</button>',fragment,re.S)
  day=next(b for b in buttons if '>Day</button>' in b);night=next(b for b in buttons if '>Night</button>' in b)
  day_class=re.search(r'class="([^"]*)"',day)[1];night_class=re.search(r'class="([^"]*)"',night)[1]
  fragment=fragment.replace(day,day.replace(day_class,night_class).replace('aria-pressed="false"','aria-pressed="true"')).replace(night,night.replace(night_class,day_class).replace('aria-pressed="true"','aria-pressed="false"'))
 fragment=rewrite(fragment)
 doc='<!doctype html><html lang="en" data-theme="day"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#faf8f2"><meta name="robots" content="noindex,nofollow"><title>Vela — '+name.title()+' · Light preview</title><style>'+css+'\n'+common+'\n'+(D/'app-light.css').read_text()+'</style></head><body>'+bar(name+'.html')+fragment+extras+'<script>'+js+'</script></body></html>'
 (OUT/(name+'.html')).write_text(doc)
manifest=dict(kind='visual-preview-only',liveProductChanged=False,sourceHomeSha256=hashlib.sha256((ROOT/'design/vela-home.html').read_bytes()).hexdigest(),references=['https://paytivo.xyz/','https://aave.com/'],pages={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.glob('*.html'))},walletConnected=False,tradingProtectionChanged=False)
(D/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest))
