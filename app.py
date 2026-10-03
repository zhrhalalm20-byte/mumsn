import os
import re
from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__, template_folder="templates")

BASE_URL = "https://nataeji.moe.gov.ye"
SEARCH_URL = f"{BASE_URL}/seat-numbers/secondary/"
PROCESS_URL = f"{BASE_URL}/seat-numbers/secondary/process/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def is_four_part_name(name):
    parts = [p for p in clean_text(name).split(" ") if p]
    return len(parts) >= 4

def absolute_url(url):
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{BASE_URL}{url}"
    return f"{BASE_URL}/{url}"

def parse_result_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    body_text = clean_text(soup.get_text())

    if "انتهت صلاحية النتائج" in body_text or "صلاحية النتائج" in body_text:
        return {"success": False, "message": "انتهت صلاحية نتيجة البحث، يرجى الاستعلام مرة أخرى."}

    seat_number = None
    psn = None
    academic_year = None
    student_name = None

    seat_match = re.search(r'رقم\s*الجلوس\s*[:：\-]?\s*([0-9٠-٩]+)', body_text)
    psn_match = re.search(r'PSN\s*[:：\-]?\s*([0-9٠-٩]+)', body_text)
    year_match = re.search(r'العام\s*الدراسي\s*[:：\-]?\s*([0-9]{4}\s*/\s*[0-9]{4})', body_text)
    name_match = re.search(r'الاسم\s*[:：\-]?\s*(.+?)(?=\s+(?:رقم\s*الجلوس|العام\s*الدراسي|PSN)|$)', body_text)

    if seat_match:
        seat_number = seat_match.group(1)
    if psn_match:
        psn = psn_match.group(1)
    if year_match:
        academic_year = clean_text(year_match.group(1))
    if name_match:
        student_name = clean_text(name_match.group(1))

    for row in soup.find_all('tr'):
        cells = [clean_text(el.get_text()) for el in row.find_all(['th', 'td'])]
        if len(cells) < 2:
            continue
        if not seat_number and "رقم الجلوس" in cells[0]:
            seat_number = cells[1]
        if not psn and "PSN" in cells[0]:
            psn = cells[1]
        if not academic_year and "العام الدراسي" in cells[0]:
            academic_year = cells[1]
        if not student_name and cells[0] == "الاسم":
            student_name = cells[1]

    if not seat_number:
        if any(msg in body_text for msg in ["لا توجد", "لم يتم العثور", "غير موجود"]):
            return {"success": False, "message": "لم يتم العثور على بيانات بهذا الاسم."}
        return {"success": False, "message": "وصلت استجابة غير متوقعة من الموقع الرسمي."}

    return {
        "success": True,
        "seat_number": seat_number,
        "psn": psn,
        "academic_year": academic_year,
        "student_name": student_name
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    academic_year = clean_text(data.get("academic_year"))
    student_name = clean_text(data.get("student_name"))

    if not academic_year or not student_name:
        return jsonify({"success": False, "message": "جميع الحقول مطلوبة."}), 400

    if not is_four_part_name(student_name):
        return jsonify({"success": False, "message": "يرجى إدخال الاسم الرباعي كاملًا."}), 400

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        # 1. طلب الصفحة الأولى لاستخراج رمز CSRF ورابط الكوكيز
        page_res = session.get(SEARCH_URL, timeout=25, headers={"Referer": BASE_URL})
        soup = BeautifulSoup(page_res.text, 'html.parser')
        csrf_input = soup.find('input', {'name': 'csrfmiddlewaretoken'})
        
        if not csrf_input or not csrf_input.get('value'):
            return jsonify({"success": False, "message": "تعذر الحصول على رمز الحماية CSRF."}), 502
            
        csrf_token = csrf_input['value']

        # 2. إرسال طلب البحث POST
        payload = {
            "academic_year": academic_year,
            "student_name": student_name,
            "csrfmiddlewaretoken": csrf_token
        }
        
        post_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRFToken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH_URL,
            "Origin": BASE_URL
        }

        process_res = session.post(PROCESS_URL, data=payload, headers=post_headers, timeout=25)
        res_json = process_res.json()

        if not res_json or not res_json.get("success") or not res_json.get("redirect_url"):
            return jsonify({"success": False, "message": res_json.get("message", "لم يتم العثور على بيانات.")}), 404

        # 3. جلب صفحة النتيجة النهائية
        redirect_url = absolute_url(res_json.get("redirect_url"))
        result_res = session.get(redirect_url, timeout=25, headers={"Referer": SEARCH_URL})
        parsed_data = parse_result_html(result_res.text)

        return jsonify(parsed_data)

    except requests.exceptions.Timeout:
        return jsonify({"success": False, "message": "استغرق الاتصال بالموقع وقتًا طويلًا."}), 504
    except Exception as e:
        return jsonify({"success": False, "message": f"حدث خطأ في النظام: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
