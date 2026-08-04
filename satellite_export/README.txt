ANH VE TINH SENTINEL-2 - 11 SITE, 01/2025 - 08/2026
====================================================
Nguon du lieu: Copernicus Sentinel-2 L2A (ESA), lay qua Earth Search / AWS Open Data.
Trich dan trong bao cao: "Copernicus Sentinel-2 (ESA)".

CACH CHON ANH
- Moi thang, moi scene duoc cham diem % may TREN DUNG AOI (doc band SCL),
  KHONG dung metadata eo:cloud_cover (metadata tinh cho ca o 110x110km).
- Anh xuat ra la scene it may nhat thang do. KHONG ghep, KHONG composite.
- Pixel goc 10m, chi stretch de hien thi (chia 3000, gamma 1.4).

TEN FILE
  YYYY-MM_<ngay chup thuc te>_cloud<XX>pct_<OK|CLOUDY>.png
  OK     = may tren AOI < 30%
  CLOUDY = >= 30%, KHONG NEN DUNG

LUU Y QUAN TRONG
1. Ngay chup trong thang KHONG deu. Vi du thang 3 co the la ngay 03,
   thang 5 la ngay 27 -> khoang cach thuc te 85 ngay chu khong phai 60.
   Khi noi suy tien do, dung ngay chup that (cot capture_date).
2. Site 01/03/04 chong lan nhau trong ban kinh ~1km. Tren anh 10m la MOT
   cong truong lien khoi, khong tach duoc dong gop tung du an.
3. Site 05 (tau dien Phu Quoc): CHUA XAC MINH huong tuyen chinh thuc.
   Toa do la suy doan theo hanh lang DT.975. Khong dung lam can cu.
4. Site 07a/07b/08/09 la tuyen dai; day chi la MOT diem dai dien
   (nut giao / cong truong / depot), khong phai ca hanh lang.
5. Site 01 va 08 la LAN BIEN -> NDVI khong dung duoc (nuoc va dat moi
   san lap deu NDVI thap). Phai dung NDWI/MNDWI, huong nguoc lai.
6. Bien dong mau giua cac thang phan lon la MUA VU, khong phai thi cong.
   So sanh YoY cung thang thay vi MoM.
7. Cac site mien Bac (07a, 09, 10) thieu nhieu thang do mua non/mu.
   Muon lap day phai dung Sentinel-1 radar.
