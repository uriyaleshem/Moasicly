# Mosaicly / שיבוץ חכם

אפליקציית Desktop מקומית לשיבוץ תלמידים בכיתות. המערכת מיועדת למורים/רכזי שכבה שרוצים לייבא קובץ תלמידים, למפות עמודות, לבדוק נתונים, להריץ שיבוץ מאוזן, לתקן ידנית ולייצא את התוצאה ל־Excel.

המידע נשמר מקומית כברירת מחדל. AI הוא אופציונלי בלבד, כבוי כברירת מחדל, ולא משמש כמנוע החלטה לשיבוץ.

## הרצה מהירה

הדרך הכי פשוטה:

```powershell
.\RUN_CLASSBALANCER.bat
```

או ישירות מפייתון:

```powershell
python -m class_balancer
```

בדיקת עשן מלאה בלי לפתוח GUI:

```powershell
.\RUN_SMOKE_TEST.bat
```

או:

```powershell
python -m class_balancer --smoke
```

## התקנת תלויות

אם האפליקציה לא נפתחת בגלל PySide6 או ספריות חסרות:

```powershell
.\INSTALL_REQUIREMENTS.bat
```

או:

```powershell
python -m pip install -r requirements.txt
```

המערכת כוללת fallback פנימי ל־CSV/XLSX, אבל מומלץ להתקין את `openpyxl`, `pandas`, ו־`rapidfuzz` לחוויית ייבוא עשירה יותר.

## קובץ דוגמה

יש קובץ תלמידים לדוגמה כאן:

```text
examples\demo_students.csv
```

אפשר לפתוח את האפליקציה, ליצור פרויקט, לבחור את הקובץ הזה במסך הייבוא, לאשר מיפוי ולהריץ שיבוץ.

## איפה נשמרים הנתונים

ברירת המחדל:

```text
%USERPROFILE%\.class_balancer\class_balancer.sqlite3
```

אפשר לשנות נתיב DB עם משתנה סביבה:

```powershell
$env:CLASS_BALANCER_DB = "D:\classmaker\class_balancer.sqlite3"
python -m class_balancer
```

קובץ ההרצה `RUN_CLASSBALANCER.bat` מגדיר אוטומטית DB בתיקיית המשתמש אם לא הוגדר נתיב אחר.

## בניית EXE

ב־Windows אפשר לבנות קובץ הרצה עצמאי בשם `Moasicly.exe`:

```powershell
.\BUILD_EXE.ps1
```

הקובץ ייווצר כאן:

```text
dist\Moasicly.exe
```

אם רוצים להפעיל AI בגרסת ה־exe, שימו קובץ `.env` באותה תיקייה של `Moasicly.exe`.

## איפה שמים Token / API Key

AI לא נדרש להרצת המערכת. השיבוץ עובד בלי טוקן.

אם רוצים להשתמש ב־AI לעזרה בלבד, אפשר להזין את הטוקן במסך הגדרות פרטיות. שמירה מהממשק כותבת את ההגדרה לשני מקומות מקומיים כדי שיהיה ברור איפה היא נשמרה:

1. קובץ משתמש מומלץ:

```text
%USERPROFILE%\.class_balancer\.env
```

2. קובץ בפרויקט:

```text
D:\classmaker\.env
```

בגרסת ה־exe אפשר גם לשים את הקובץ ליד:

```text
Moasicly.exe
```

אפשר גם לערוך ידנית את `.env` לפי `.env.example`:

```env
CLASS_BALANCER_AI_ENABLED=false
CLASS_BALANCER_AI_PROVIDER=OpenAI
OPENAI_API_KEY=sk-your-token-here
ANTHROPIC_API_KEY=
GEMINI_API_KEY=

# Optional model overrides:
OPENAI_MODEL=gpt-4.1-mini
ANTHROPIC_MODEL=claude-3-5-sonnet-latest
GEMINI_MODEL=gemini-1.5-flash
```

שמות המשתנים:

- OpenAI: `OPENAI_API_KEY`
- Anthropic: `ANTHROPIC_API_KEY`
- Gemini: `GEMINI_API_KEY`

גם כשיש טוקן, המערכת לא שולחת שמות תלמידים ולא שולחת קובץ מלא. בנוסף להגדרה הגלובלית, לכל פרויקט יש הרשאת AI נפרדת במסך הפרויקט; בלי ההרשאה הזאת ייווצר ניתוח מקומי בלבד. במסך ההגדרות אפשר לראות את מטען הנתונים האנונימי לפני שימוש, לבדוק ספק יחיד או את כל שלושת הספקים, ולראות האם בפועל נעשה שימוש ב־AI או שנוצר ניתוח מקומי בלבד.

## מה מומש

קיים מסמך תאימות מלא:

```text
docs\spec_compliance.md
```

בקצרה:

- פרויקטים ו־SQLite מקומי.
- ייבוא CSV/XLSX כולל בחירת גיליון.
- Drag & Drop לקובץ.
- מיפוי עמודות אוטומטי.
- תבניות מיפוי.
- בדיקת נתונים ונרמול ערכים.
- עריכת תלמידים מתוך הממשק.
- מנוע שיבוץ מקומי.
- איזון גודל כיתות, מגדר, ציונים, התנהגות, חברים ובתי ספר.
- כיתות מותרות/אסורות.
- תלמידים שחייבים להיות יחד / אסור שיהיו יחד.
- גרסאות שיבוץ.
- תיקונים ידניים, החלפה, נעילה, ביטול פעולה ושחזור פעולה.
- החלפה חכמה.
- דוח איכות.
- ייצוא Excel רב־גיליוני.
- הגדרות פרטיות ו־AI אנונימי בלבד.

## בדיקות פיתוח

```powershell
python -m unittest discover -s tests
```

בדיקת טעינת QML מתבצעת כחלק מהבדיקות הידניות שבוצעו במהלך הפיתוח. אם רוצים לבדוק ידנית:

```powershell
python -m class_balancer
```

## פתרון תקלות

אם החלון לא נפתח:

```powershell
python -m pip install -r requirements.txt
python -m class_balancer
```

אם עברית נראית מוזר במסוף PowerShell, זו בדרך כלל בעיית תצוגת קונסול בלבד. הקבצים עצמם נשמרים UTF‑8.

אם רוצים להתחיל נקי, אפשר למחוק או להעביר את קובץ ה־SQLite:

```text
%USERPROFILE%\.class_balancer\class_balancer.sqlite3
```
