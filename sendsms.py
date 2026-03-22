import urllib.request, urllib.parse, json

AT_USERNAME = 'sandbox'
AT_API_KEY  = 'atsk_1628153fb623a36ae2dc4c0e34f517d4a2afa8b0f597cea7589f1eabc3dde0394974070c'
MESSAGE     = 'The Bus is about to reach Kabalagala drop off point in about 2 mins.!'
NUMBERS     = ['+256783816444', '+256763724576', '+256757072730']

for phone in NUMBERS:
    data = urllib.parse.urlencode({'username':AT_USERNAME,'to':phone,'message':MESSAGE}).encode()
    req  = urllib.request.Request(
        'https://api.sandbox.africastalking.com/version1/messaging',
        data=data, method='POST',
        headers={'apiKey':AT_API_KEY,'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res   = json.loads(r.read().decode())
            recips= res.get('SMSMessageData',{}).get('Recipients',[])
            st    = recips[0].get('status','?') if recips else '?'
            print(f'{phone} -> {st}')
    except Exception as e:
        print(f'{phone} -> ERROR: {e}')
