@echo off
REM הגדרת משימה יומית להרצת סורק ההגרלות בשעה 09:00
schtasks /create /tn "הגרלות מחיר מטרה" /tr "python \"C:\Users\liory\OneDrive\Desktop\הגרלות\lottery_check.py\"" /sc daily /st 09:00 /f
echo.
echo המשימה נוצרה! הסקריפט ירוץ כל יום בשעה 09:00
echo הדוח יישמר ב: C:\Users\liory\OneDrive\Desktop\הגרלות\lottery_report.html
pause
