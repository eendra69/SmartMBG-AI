import streamlit as st
import gspread
import pandas as pd
from pulp import *

# Konfigurasi Halaman Streamlit
st.set_page_config(page_title="Optimasi Menu MBG", layout="wide")
st.title("🍲 Smart Menu Builder Guide (Smart MBG) - Aplikasi Optimasi Menu MBG")

SHEET_URL = "https://docs.google.com/spreadsheets/d/1RaGjYtKssJOH6tS1kDH3x4LXM-DNi4jNJn26RZi-5aM/"

# ==========================================
# FUNGSI CACHE UNTUK MENGAMBIL DATA
# ==========================================
@st.cache_data(ttl=600) # Cache data selama 10 menit agar tidak lemot
def load_data():
    # Membaca kredensial dari st.secrets
    credentials = dict(st.secrets["gcp_service_account"])
    gc = gspread.service_account_from_dict(credentials)
    sh = gc.open_by_url(SHEET_URL)
    
    df_menu = pd.DataFrame(sh.worksheet('menu_master').get_all_records())
    df_recipe = pd.DataFrame(sh.worksheet('recipe_details').get_all_records())
    df_ingredients = pd.DataFrame(sh.worksheet('ingredients').get_all_records())
    df_nutrition = pd.DataFrame(sh.worksheet('nutrition_rules').get_all_records())
    df_leftover = pd.DataFrame(sh.worksheet('leftover_history').get_all_records())
    df_prices = pd.DataFrame(sh.worksheet('local_prices').get_all_records())
    df_inventory = pd.DataFrame(sh.worksheet('inventory_stock').get_all_records())
    
    return df_menu, df_recipe, df_ingredients, df_nutrition, df_leftover, df_prices, df_inventory

# Load data dengan error handling
try:
    with st.spinner('Menghubungkan ke Google Sheets...'):
        df_menu, df_recipe, df_ingredients, df_nutrition, df_leftover, df_prices, df_inventory = load_data()
    st.success("✅ Data berhasil dimuat dari Google Sheets!")
except Exception as e:
    st.error(f"Gagal mengambil data: {e}")
    st.stop()

# ==========================================
# SIDEBAR: PARAMETER INPUT PENGGUNA
# ==========================================
st.sidebar.header("⚙️ Parameter Optimasi")

# FITUR BARU: Pemilihan Jumlah Hari
JUMLAH_HARI = st.sidebar.selectbox("Jumlah Hari Penyajian", [4, 5, 6], index=2, help="Pilih berapa hari menu disajikan dalam seminggu.")

JENJANG = st.sidebar.selectbox("Tingkat Sekolah", ["SD", "SMP", "SMA"], index=0)
N_SISWA = st.sidebar.number_input("Jumlah Siswa", min_value=1, value=300)

# Default budget disesuaikan otomatis berdasarkan jumlah hari (Asumsi dasar 2.5jt per hari)
default_budget = int(JUMLAH_HARI * 2500000)
BUDGET_MINGGUAN = st.sidebar.number_input(f"Budget {JUMLAH_HARI} Hari (Rp)", min_value=100000, value=default_budget, step=100000)

PENALTI_GIZI = st.sidebar.number_input("Penalti per poin selisih target gizi (Rp)", min_value=0.0, value=5.0)
PENALTI_LEFTOVER = st.sidebar.number_input("Penalti per 1% sisa makanan (Rp)", min_value=0.0, value=100.0)

# ==========================================
# Agen AI Pemilah harga
# ==========================================
class PriceSelectionAgent:
    def __init__(self, df_prices):
        self.df = df_prices.copy()

    def clean_prices(self, row):
        """
        Ambil semua harga valid (>0 dan bukan NaN)
        """
        harga_list = [
            row.get('harga koperasi', None),
            row.get('harga pasar', None),
            row.get('harga vendor lokal', None)
        ]

        valid_prices = [
            h for h in harga_list 
            if pd.notna(h) and h > 0
        ]

        return valid_prices

    def select_best_price(self):
        """
        Pilih harga termurah per bahan
        """
        best_prices = []

        for _, row in self.df.iterrows():
            valid_prices = self.clean_prices(row)

            if len(valid_prices) == 0:
                harga_terpilih = 0  # fallback
                sumber = "tidak tersedia"
            else:
                harga_terpilih = min(valid_prices)

                if harga_terpilih == row.get('harga koperasi'):
                    sumber = "koperasi"
                elif harga_terpilih == row.get('harga pasar'):
                    sumber = "pasar"
                else:
                    sumber = "vendor lokal"

            best_prices.append({
                "nama bahan": row['nama bahan'],
                "harga_terpilih": harga_terpilih,
                "sumber_harga": sumber
            })

        return pd.DataFrame(best_prices)

# ==========================================
# PRA-PEMROSESAN DATA
# ==========================================
# PRICE AGENT ACTIVATION
price_agent = PriceSelectionAgent(df_prices)
df_best_price = price_agent.select_best_price()

# Merge dengan recipe
df_recipe_price = pd.merge(
    df_recipe, 
    df_best_price, 
    left_on='nama_bahan', 
    right_on='nama bahan', 
    how='left'
)

# Hitung biaya pakai harga terbaik
df_recipe_price['biaya_bahan'] = (
    df_recipe_price['berat_per_porsi'] / 1000
) * df_recipe_price['harga_terpilih']


biaya_per_menu = df_recipe_price.groupby('id_menu')['biaya_bahan'].sum().to_dict()

df_recipe_nutrisi = pd.merge(df_recipe, df_ingredients, left_on='nama_bahan', right_on='nama bahan', how='left')
df_recipe_nutrisi['kcal'] = (df_recipe_nutrisi['berat_per_porsi'] / 100) * df_recipe_nutrisi['kandungan kcal per 100g']
df_recipe_nutrisi['protein'] = (df_recipe_nutrisi['berat_per_porsi'] / 100) * df_recipe_nutrisi['kandungan protein per 100 g']
df_recipe_nutrisi['karbo'] = (df_recipe_nutrisi['berat_per_porsi'] / 100) * df_recipe_nutrisi['kandungan karbohidrat per 100g']
df_recipe_nutrisi['lemak'] = (df_recipe_nutrisi['berat_per_porsi'] / 100) * df_recipe_nutrisi['kandungan lemak per 100g']
nutrisi_per_menu = df_recipe_nutrisi.groupby('id_menu')[['kcal', 'protein', 'karbo', 'lemak']].sum().to_dict('index')

df_menu['biaya'] = df_menu['id_menu'].map(biaya_per_menu).fillna(0)
df_menu['kcal'] = df_menu['id_menu'].map(lambda x: nutrisi_per_menu.get(x, {}).get('kcal', 0))
df_menu['protein'] = df_menu['id_menu'].map(lambda x: nutrisi_per_menu.get(x, {}).get('protein', 0))
df_menu['karbo'] = df_menu['id_menu'].map(lambda x: nutrisi_per_menu.get(x, {}).get('karbo', 0))
df_menu['lemak'] = df_menu['id_menu'].map(lambda x: nutrisi_per_menu.get(x, {}).get('lemak', 0))

leftover_dict = df_leftover.set_index('nama_menu')['persentase_leftover_Li'].to_dict()
df_menu['leftover_pct'] = df_menu['nama_menu'].map(leftover_dict).fillna(0)

id_to_nama = df_menu.set_index('id_menu')['nama_menu'].to_dict()
df_recipe['nama_menu'] = df_recipe['id_menu'].map(id_to_nama)

# PENGATURAN HARI DINAMIS
HARI_FULL = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"]
HARI = HARI_FULL[:JUMLAH_HARI] # Otomatis memotong array hari sesuai input pengguna

KATEGORI = ["Menu Pokok", "Protein Hewani", "Protein Nabati", "Sayur", "Buah"]
ZAT_GIZI = ['kcal', 'protein', 'karbo', 'lemak']

target_gizi = df_nutrition[df_nutrition['tingkat sekolah'] == JENJANG].iloc[0]
target = {
    'kcal': target_gizi['kebutuhan kcal'],
    'protein': target_gizi['kebutuhan protein'],
    'karbo': target_gizi['kebutuhan karbohidrat'],
    'lemak': target_gizi['kebutuhan lemak']
}

# ================================
# PRECOMPUTE SUPER FAST LOOKUP
# ================================

menu_list = df_menu['nama_menu'].tolist()

biaya_dict = df_menu.set_index('nama_menu')['biaya'].to_dict()
leftover_dict = df_menu.set_index('nama_menu')['leftover_pct'].to_dict()

# Nutrisi dictionary
nutrisi_dict = df_menu.set_index('nama_menu')[['kcal','protein','karbo','lemak']].to_dict('index')

# Kategori mapping
kategori_dict = df_menu.set_index('nama_menu')['kategori'].to_dict()

# Metode masak
metode_dict = df_menu.set_index('nama_menu')['metode_masak'].to_dict()

# Group kategori → menu list
kategori_menu = {
    kat: df_menu[df_menu['kategori'] == kat]['nama_menu'].tolist()
    for kat in KATEGORI
}

# Group metode → menu list
metode_menu = {
    m: df_menu[df_menu['metode_masak'] == m]['nama_menu'].tolist()
    for m in df_menu['metode_masak'].unique()
}

# Inventory usage precompute
bahan_usage = {}
for bahan in df_inventory['nama_bahan'].unique():
    usage_per_menu = {}
    for i in menu_list:
        total = df_recipe[
            (df_recipe['nama_bahan'] == bahan) & 
            (df_recipe['nama_menu'] == i)
        ]['berat_per_porsi'].sum()
        if total > 0:
            usage_per_menu[i] = total / 1000
    bahan_usage[bahan] = usage_per_menu

# ==========================================
# TOMBOL EKSEKUSI
# ==========================================
if st.button("🚀 Buat Jadwal Menu!", type="primary"):
    with st.spinner("Mesin AI sedang menghitung jutaan kombinasi menu..."):
        
        # Nama model otomatis menyesuaikan hari
        model = LpProblem(f"Optimasi_MBG_{JUMLAH_HARI}Hari", LpMinimize)

        x = LpVariable.dicts("x", (menu_list, HARI), cat='Binary')
        shortage = LpVariable.dicts("s", (ZAT_GIZI, HARI), lowBound=0)

        # ================================
        # OBJECTIVE FUNCTION (FAST)
        # ================================
        model += lpSum(
            biaya_dict[i] * N_SISWA * x[i][t]
            + leftover_dict[i] * 100 * PENALTI_LEFTOVER * x[i][t]
            for i in menu_list for t in HARI
        ) + lpSum(
            shortage[k][t] * PENALTI_GIZI
            for k in ZAT_GIZI for t in HARI
        )

        # ================================
        # CONSTRAINTS
        # ================================

        for t in HARI:

            # 1. Kategori constraint
            for kat in KATEGORI:
                model += lpSum(x[i][t] for i in kategori_menu[kat]) == 1

            # 2. Nutrisi constraint
            for k in ZAT_GIZI:
                kandungan = lpSum(nutrisi_dict[i][k] * x[i][t] for i in menu_list)

                model += kandungan >= 0.6 * target[k]
                model += kandungan + shortage[k][t] >= target[k]

                if k == 'karbo':
                    model += kandungan >= 45

            # 3. Metode masak constraint
            for m, menus in metode_menu.items():
                if m != "Tanpa Masak":
                    model += lpSum(x[i][t] for i in menus) <= 2


        # 4. Budget constraint
        model += lpSum(
            biaya_dict[i] * N_SISWA * x[i][t]
            + leftover_dict[i] * 100 * PENALTI_LEFTOVER * x[i][t]
            for i in menu_list for t in HARI
        ) + lpSum(
            shortage[k][t] * PENALTI_GIZI
            for k in ZAT_GIZI for t in HARI
        ) <= BUDGET_MINGGUAN


        # 5. Maksimal muncul 2x
        for i in menu_list:
            model += lpSum(x[i][t] for t in HARI) <= 1

        # 6. Pembatas: Menu yang sama tidak boleh muncul berurutan hari
        for i in menu_list:
            for d in range(len(HARI) - 1):
                hari_ini = HARI[d]
                besok = HARI[d+1]
                model += x[i][hari_ini] + x[i][besok] <= 1

        # 7. Inventory constraint (SUPER OPTIMIZED)
        for bahan, usage_dict in bahan_usage.items():

            row = df_inventory[df_inventory['nama_bahan'] == bahan].iloc[0]
            batas = row['stok_saat_ini'] - row['buffer_stock']

            model += lpSum(
                usage_dict.get(i, 0) * N_SISWA * x[i][t]
                for i in menu_list for t in HARI
            ) <= batas

        # Solve Model
        model.solve(PULP_CBC_CMD(msg=0))

        if LpStatus[model.status] == 'Optimal':
            st.success(f"✨ JADWAL MENU UNTUK {JUMLAH_HARI} HARI BERHASIL DIBUAT!")
            
            jadwal_utama, jadwal_detail = [], []
            for t in HARI:
                menu_hari_ini = [i for i in menu_list if value(x[i][t]) == 1]
                menu_terurut, berat_per_kategori = [], []

                for kat in KATEGORI:
                    for m in menu_hari_ini:
                        if df_menu[df_menu['nama_menu'] == m]['kategori'].values[0] == kat:
                            menu_terurut.append(m)
                            id_m = df_menu[df_menu['nama_menu'] == m]['id_menu'].values[0]
                            berat = df_recipe[df_recipe['id_menu'] == id_m]['berat_per_porsi'].sum()
                            berat_per_kategori.append(f"{berat:.0f}g")
                            break

                biaya_hari = sum([df_menu[df_menu['nama_menu'] == m]['biaya'].values[0] * N_SISWA for m in menu_hari_ini])
                tot_kcal = sum([df_menu[df_menu['nama_menu'] == m]['kcal'].values[0] for m in menu_hari_ini])
                tot_karbo = sum([df_menu[df_menu['nama_menu'] == m]['karbo'].values[0] for m in menu_hari_ini])
                tot_protein = sum([df_menu[df_menu['nama_menu'] == m]['protein'].values[0] for m in menu_hari_ini])
                tot_lemak = sum([df_menu[df_menu['nama_menu'] == m]['lemak'].values[0] for m in menu_hari_ini])

                jadwal_utama.append([t] + menu_terurut + [f"Rp {biaya_hari:,.0f}"])
                jadwal_detail.append([t] + berat_per_kategori + [f"{tot_kcal:.1f}", f"{tot_karbo:.1f}", f"{tot_protein:.1f}", f"{tot_lemak:.1f}"])

            st.subheader("📋 Tabel 1: Daftar Menu dan Biaya Harian")
            df_utama = pd.DataFrame(jadwal_utama, columns=["Hari"] + KATEGORI + ["Biaya Harian"])
            st.dataframe(df_utama, use_container_width=True)
            
            st.subheader("⚖️ Tabel 2: Detail Berat Porsi & Akumulasi Gizi")
            kolom_detail = ["Hari", "Berat M.Pokok", "Berat P.Hewani", "Berat P.Nabati", "Berat Sayur", "Berat Buah", "Total Kcal", "Karbo (g)", "Protein (g)", "Lemak (g)"]
            df_detail = pd.DataFrame(jadwal_detail, columns=kolom_detail)
            st.dataframe(df_detail, use_container_width=True)

# ==========================================
# TABEL 3: BELANJA BERDASARKAN SUMBER
# ==========================================
st.subheader("🛒 Tabel 3: Kebutuhan Belanja per Sumber Supplier")

data_belanja = []

for t in HARI:
    menu_hari_ini = [i for i in menu_list if value(x[i][t]) == 1]

    for m in menu_hari_ini:
        resep_m = df_recipe_price[df_recipe_price['nama_menu'] == m]

        for _, row in resep_m.iterrows():
            bahan = row['nama_bahan']
            sumber = row['sumber_harga']
            berat_per_porsi = row['berat_per_porsi']

            kebutuhan_kg = (berat_per_porsi * N_SISWA) / 1000

            data_belanja.append({
                "Nama Bahan": bahan,
                "Hari": t,
                "Sumber": sumber,
                "Kebutuhan (Kg)": kebutuhan_kg
            })

if data_belanja:
    df_belanja = pd.DataFrame(data_belanja)

    # Pivot: bahan + sumber → hari
    df_pivot = df_belanja.groupby(
        ['Sumber', 'Nama Bahan', 'Hari']
    )['Kebutuhan (Kg)'].sum().unstack(fill_value=0)

    # Urutkan kolom hari
    kolom_hari_ada = [hari for hari in HARI if hari in df_pivot.columns]
    df_pivot = df_pivot[kolom_hari_ada]

    # Total mingguan
    df_pivot[f'Total {JUMLAH_HARI} Hari (Kg)'] = df_pivot.sum(axis=1)

    st.dataframe(df_pivot.style.format("{:.2f}"), use_container_width=True)
else:
    st.info("Data belanja tidak tersedia.")
    
for sumber in df_belanja['Sumber'].unique():
    st.markdown(f"### 📦 Supplier: {sumber.upper()}")

    df_sup = df_belanja[df_belanja['Sumber'] == sumber]

    df_pivot_sup = df_sup.groupby(
        ['Nama Bahan', 'Hari']
    )['Kebutuhan (Kg)'].sum().unstack(fill_value=0)

    df_pivot_sup[f'Total {JUMLAH_HARI} Hari (Kg)'] = df_pivot_sup.sum(axis=1)

    st.dataframe(df_pivot_sup.style.format("{:.2f}"), use_container_width=True)

            # ==========================================
            # AKHIR TABEL 3
            # ==========================================

            # Menghitung total biaya dari menu yang final terpilih
            total_biaya_aktual = 0
            for t in HARI:
                for i in menu_list:
                    total_biaya_aktual += biaya_dict[i] * N_SISWA * value(x[i][t])
            
            # Teks metric otomatis
            st.metric(label=f"Total Biaya {JUMLAH_HARI} Hari", value=f"Rp {total_biaya_aktual:,.0f}")

        else:
            st.error("❌ Model Infeasible: Tidak ada kombinasi menu yang memenuhi syarat.")
            st.info("Saran perbaikan: 1) Naikkan Budget, 2) Periksa stok bahan di gudang untuk jumlah siswa tersebut, atau 3) Longgarkan target gizi.")
