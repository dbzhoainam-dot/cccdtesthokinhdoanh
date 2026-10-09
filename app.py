import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from PIL import Image
import json
import re
import pandas as pd

# ================= CẤU HÌNH GIAO DIỆN =================
st.set_page_config(page_title="Đối soát CCCD & ĐKKD", layout="wide")

BASE_URL = "http://tracuunnt.gdt.gov.vn/tcnnt/mstcn.jsp"
CAPTCHA_URL = "http://tracuunnt.gdt.gov.vn/tcnnt/captcha.png"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": BASE_URL
}

# ================= CÁC HÀM XỬ LÝ CỔNG THUẾ =================
def get_new_tax_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(BASE_URL, timeout=8)
        cap_res = session.get(CAPTCHA_URL, timeout=8)
        return session, cap_res.content
    except Exception as e:
        st.error(f"Lỗi kết nối cổng Thuế: {e}")
        return None, None

def refresh_captcha_only():
    if st.session_state.tax_session:
        try:
            res = st.session_state.tax_session.get(CAPTCHA_URL, timeout=8)
            st.session_state.captcha_bytes = res.content
        except:
            pass

def execute_query(session, cccd, captcha_text):
    payload = {
        "cmt": cccd.strip(),
        "captcha": captcha_text.strip(),
        "mst": "",
        "fullname": "",
        "address": ""
    }
    try:
        res = session.post(BASE_URL, data=payload, timeout=12)
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
        
        text = soup.get_text()
        if "Mã xác nhận không đúng" in text or "Vui lòng nhập đúng" in text:
            return "WRONG_CAPTCHA", []
            
        table = soup.find("table", class_="ta_border")
        if not table:
            return "NOT_FOUND", []
            
        rows = table.find_all("tr")
        records = []
        for r in rows[1:]:
            cols = [td.get_text(strip=True) for td in r.find_all("td")]
            if len(cols) >= 6:
                records.append({
                    "mst": cols[1],
                    "ten_nnt": cols[2],
                    "co_quan_thue": cols[3],
                    "cmnd": cols[4],
                    "trang_thai": cols[6] if len(cols) > 6 else "Không có ghi chú"
                })
        return "SUCCESS", records
    except Exception as e:
        return f"ERROR: {e}", []

# ================= HÀM GỌI GEMINI MODEL MỚI NHẤT =================
def call_gemini_vision(img, prompt):
    candidates = [
        "gemini-3.8-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash"
    ]
    last_err = None
    for m in candidates:
        try:
            model = genai.GenerativeModel(m)
            response = model.generate_content([prompt, img])
            if response and response.text:
                return response.text
        except Exception as e:
            last_err = e
            continue
            
    # Dự phòng: tự động tìm model có hỗ trợ generateContent
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                model = genai.GenerativeModel(m.name)
                response = model.generate_content([prompt, img])
                return response.text
    except Exception as e:
        last_err = e
        
    raise last_err

# ================= QUẢN LÝ BỘ NHỚ STREAMLIT =================
if "data_list" not in st.session_state:
    st.session_state.data_list = []
if "current_idx" not in st.session_state:
    st.session_state.current_idx = 0
if "results_log" not in st.session_state:
    st.session_state.results_log = []
if "tax_session" not in st.session_state:
    st.session_state.tax_session = None
if "captcha_bytes" not in st.session_state:
    st.session_state.captcha_bytes = None

# ================= SIDEBAR CẤU HÌNH =================
with st.sidebar:
    st.header("Cấu hình")
    gemini_key = st.text_input("Nhập Gemini API Key:", type="password")
    if gemini_key:
        genai.configure(api_key=gemini_key)
    
    st.markdown("---")
    if st.button("Làm mới / Bắt đầu lại"):
        st.session_state.data_list = []
        st.session_state.current_idx = 0
        st.session_state.results_log = []
        st.session_state.tax_session = None
        st.session_state.captcha_bytes = None
        st.rerun()

st.title("Đối Soát CCCD & Đăng Ký Kinh Doanh (Bán Tự Động)")

# ================= BƯỚC 1: TẢI ẢNH VÀ QUÉT OCR =================
if not st.session_state.data_list:
    st.subheader("1. Tải lên ảnh (viết tay hoặc in)")
    upload_file = st.file_uploader("Chọn ảnh giấy tờ / bảng kê", type=["jpg", "png", "jpeg"])
    
    if upload_file:
        img = Image.open(upload_file)
        c1, c2 = st.columns([1, 1])
        with c1:
            st.image(img, caption="Ảnh đầu vào", use_container_width=True)
        with c2:
            if st.button("🚀 Trích xuất danh sách CCCD & Họ tên", type="primary"):
                if not gemini_key:
                    st.warning("Vui lòng nhập API Key ở menu bên trái.")
                else:
                    with st.spinner("Đang đọc chữ viết tay và trích xuất dữ liệu..."):
                        try:
                            prompt = """
                            Bạn là chuyên gia OCR văn bản tiếng Việt.
                            Nhiệm vụ: Trích xuất toàn bộ:
                            - Họ và tên người khai báo (nếu không có tên ghi rõ trên giấy thì để rỗng "").
                            - Số CMND hoặc CCCD (chuỗi gồm 9 hoặc 12 chữ số).
                            
                            Trả về DUY NHẤT một chuỗi JSON hợp lệ dạng danh sách các object:
                            [{"cccd": "083181010290", "ho_ten": "NGUYỄN VĂN A"}]
                            Không giải thích thêm.
                            """
                            raw_out = call_gemini_vision(img, prompt)
                            cleaned = re.sub(r'```json\s*|\s*```', '', raw_out.strip())
                            data = json.loads(cleaned)
                            
                            if data:
                                st.session_state.data_list = data
                                st.session_state.current_idx = 0
                                s, c = get_new_tax_session()
                                st.session_state.tax_session = s
                                st.session_state.captcha_bytes = c
                                st.success(f"Đã trích xuất thành công {len(data)} mục!")
                                st.rerun()
                            else:
                                st.warning("Không tìm thấy số CCCD nào trong ảnh.")
                        except Exception as err:
                            st.error(f"Lỗi xử lý OCR: {err}")

# ================= BƯỚC 2: TRA CỨU BÁN TỰ ĐỘNG =================
else:
    total = len(st.session_state.data_list)
    idx = st.session_state.current_idx
    
    if idx < total:
        item = st.session_state.data_list[idx]
        st.progress(idx / total, text=f"Đang xử lý mục {idx + 1} / {total}")
        
        col_declared, col_act = st.columns([1, 1])
        with col_declared:
            st.markdown(f"""
            ### Thông tin khai báo trên giấy:
            * **Họ và tên:** `{item.get('ho_ten', '') or '(Không có tên)'}`
            * **Số CCCD/CMND:** `{item.get('cccd', '')}`
            """)
            
        with col_act:
            st.markdown("### Nhập mã Captcha:")
            if st.session_state.captcha_bytes:
                st.image(st.session_state.captcha_bytes, width=170)
            else:
                s, c = get_new_tax_session()
                st.session_state.tax_session = s
                st.session_state.captcha_bytes = c
                st.rerun()
                
            with st.form(key=f"lookup_form_{idx}"):
                captcha_val = st.text_input("Gõ mã và nhấn Enter:", max_chars=8)
                submitted = st.form_submit_button("Xác nhận & Chuyển tiếp", type="primary")
                
                if submitted:
                    if not captcha_val:
                        st.warning("Chưa nhập mã xác nhận.")
                    else:
                        status, recs = execute_query(
                            st.session_state.tax_session,
                            item['cccd'],
                            captcha_val
                        )
                        
                        if status == "WRONG_CAPTCHA":
                            st.error("Mã Captcha sai! Vui lòng nhập mã mới.")
                            refresh_captcha_only()
                            st.rerun()
                        else:
                            has_record = len(recs) > 0
                            tax_name = recs[0]['ten_nnt'] if has_record else "Không tìm thấy"
                            dec_name = (item.get('ho_ten') or "").strip().upper()
                            
                            if not has_record:
                                is_match = "Không có dữ liệu"
                            elif not dec_name:
                                is_match = "Chưa có tên đối chiếu"
                            elif dec_name in tax_name.upper() or tax_name.upper() in dec_name:
                                is_match = "Khớp"
                            else:
                                is_match = "LỆCH TÊN"
                            
                            st.session_state.results_log.append({
                                "STT": idx + 1,
                                "CCCD Bảng Kê": item['cccd'],
                                "Tên Bảng Kê": item.get('ho_ten', ''),
                                "Tên Trên Thuế": tax_name,
                                "Đối Soát Tên": is_match,
                                "Mã Số Thuế": recs[0]['mst'] if has_record else "—",
                                "Tình Trạng Hoạt Động": recs[0]['trang_thai'] if has_record else "Không tồn tại ĐKKD/Thuế",
                                "Cơ Quan Quản Lý": recs[0]['co_quan_thue'] if has_record else "—"
                            })
                            
                            st.session_state.current_idx += 1
                            refresh_captcha_only()
                            st.rerun()
    else:
        st.success("🎉 Hoàn tất tra cứu toàn bộ danh sách!")

    # ================= BẢNG KẾT QUẢ =================
    if st.session_state.results_log:
        st.markdown("---")
        st.subheader("Bảng đối soát chứng từ")
        df = pd.DataFrame(st.session_state.results_log)
        
        def highlight_match(val):
            if val == "Khớp":
                return "background-color: #d4edda; color: #155724; font-weight: bold;"
            elif val == "LỆCH TÊN":
                return "background-color: #f8d7da; color: #721c24; font-weight: bold;"
            return ""

        st.dataframe(df.style.applymap(highlight_match, subset=["Đối Soát Tên"]), use_container_width=True)
        
        csv_data = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 Tải kết quả đối soát (Excel/CSV)", csv_data, "doi_soat_cccd.csv", "text/csv")
