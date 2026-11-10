# -*- coding: utf-8 -*-
import os, smtplib, ssl
from email.mime.text import MIMEText
from email.header import Header
from pathlib import Path
host, port = os.environ['SMTP_SERVER'], int(os.environ.get('SMTP_PORT', '465'))
user, pwd = os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'].replace(' ', '')
sender, to = os.environ.get('ALERT_EMAIL_FROM') or user, [x.strip() for x in os.environ['ALERT_EMAIL_TO'].split(',') if x.strip()]
msg = MIMEText(Path('email_body.html').read_text(encoding='utf-8'), 'html', 'utf-8')
msg['Subject'] = Header(Path('email_subject.txt').read_text(encoding='utf-8'), 'utf-8')
msg['From'], msg['To'] = sender, ', '.join(to)
ctx = ssl.create_default_context()
if port == 465:
    with smtplib.SMTP_SSL(host, port, context=ctx) as s: s.login(user, pwd); s.sendmail(sender, to, msg.as_string())
else:
    with smtplib.SMTP(host, port) as s: s.starttls(context=ctx); s.login(user, pwd); s.sendmail(sender, to, msg.as_string())
print('sent to', len(to), 'recipient(s)')
