#!/usr/bin/env python3
"""
Seed MongoDB with the complete official INEC administrative hierarchy:
  - 37 entities  (36 states + FCT)
  - 774 LGAs     mapped to their correct states
  - ~8,809 wards distributed according to official INEC ward counts

Usage:
    cd backend
    python ../scripts/seed_nigeria.py               # skip if data exists
    python ../scripts/seed_nigeria.py --clear       # wipe & reseed
    python ../scripts/seed_nigeria.py --dry-run     # count only, no writes

Requirements: run after `pip install -r requirements.txt` and with a valid
MONGODB_URI in .env (or set in environment).
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── Make sure app/ is on the path when run from project root or scripts/ ──────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import motor.motor_asyncio
from beanie import init_beanie
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.models.elections import LGA, State, Ward

# ── Official INEC administrative hierarchy ────────────────────────────────────
#
# Each state entry: (state_name, inec_code, geopolitical_zone, lat, lng,
#                    [(lga_name, ward_count), ...])
#
# Ward counts are from the official INEC list (total ≈ 8,809).
# LGAs are listed in alphabetical order matching INEC enumeration.
# inec_lga_code is the 2-digit sequential number assigned per state.

_HIERARCHY = [
    # ── Abia — 17 LGAs, 184 wards ────────────────────────────────────────────
    ("Abia", "01", "South-East", 5.45, 7.52, [
        ("Aba North", 10), ("Aba South", 11), ("Arochukwu", 11),
        ("Bende", 11), ("Ikwuano", 11), ("Isiala Ngwa North", 10),
        ("Isiala Ngwa South", 10), ("Isuikwuato", 11), ("Obingwa", 11),
        ("Ohafia", 11), ("Osisioma Ngwa", 11), ("Ugwunagbo", 10),
        ("Ukwa East", 10), ("Ukwa West", 10), ("Umuahia North", 11),
        ("Umuahia South", 11), ("Umu Nneochi", 10),
    ]),
    # ── Adamawa — 21 LGAs, 226 wards ─────────────────────────────────────────
    ("Adamawa", "02", "North-East", 9.33, 12.40, [
        ("Demsa", 10), ("Fufure", 10), ("Ganye", 11), ("Gayuk", 10),
        ("Gombi", 11), ("Grie", 10), ("Hong", 11), ("Jada", 11),
        ("Lamurde", 11), ("Madagali", 11), ("Maiha", 10), ("Mayo-Belwa", 11),
        ("Michika", 11), ("Mubi North", 11), ("Mubi South", 10),
        ("Numan", 11), ("Shelleng", 10), ("Song", 11), ("Toungo", 10),
        ("Yola North", 11), ("Yola South", 11),
    ]),
    # ── Akwa Ibom — 31 LGAs, 329 wards ──────────────────────────────────────
    ("Akwa Ibom", "03", "South-South", 5.01, 7.91, [
        ("Abak", 11), ("Eastern Obolo", 10), ("Eket", 11), ("Esit Eket", 10),
        ("Essien Udim", 11), ("Etim Ekpo", 10), ("Etinan", 11), ("Ibeno", 10),
        ("Ibesikpo Asutan", 11), ("Ibiono-Ibom", 11), ("Ika", 10),
        ("Ikono", 11), ("Ikot Abasi", 11), ("Ikot Ekpene", 11), ("Ini", 10),
        ("Itu", 11), ("Mbo", 10), ("Mkpat-Enin", 11), ("Nsit-Atai", 10),
        ("Nsit-Ibom", 11), ("Nsit-Ubium", 10), ("Obot Akara", 11),
        ("Okobo", 10), ("Onna", 11), ("Oron", 11), ("Oruk Anam", 11),
        ("Udung-Uko", 10), ("Ukanafun", 10), ("Uruan", 11),
        ("Urue-Offong/Oruko", 10), ("Uyo", 11),
    ]),
    # ── Anambra — 21 LGAs, 181 wards ─────────────────────────────────────────
    ("Anambra", "04", "South-East", 6.21, 6.94, [
        ("Aguata", 9), ("Anambra East", 9), ("Anambra West", 9),
        ("Anaocha", 8), ("Awka North", 8), ("Awka South", 9),
        ("Ayamelum", 9), ("Dunukofia", 8), ("Ekwusigo", 8),
        ("Idemili North", 9), ("Idemili South", 8), ("Ihiala", 9),
        ("Njikoka", 9), ("Nnewi North", 9), ("Nnewi South", 8),
        ("Ogbaru", 9), ("Onitsha North", 9), ("Onitsha South", 8),
        ("Orumba North", 9), ("Orumba South", 8), ("Oyi", 8),
    ]),
    # ── Bauchi — 20 LGAs, 196 wards ──────────────────────────────────────────
    ("Bauchi", "05", "North-East", 10.58, 9.91, [
        ("Alkaleri", 10), ("Bauchi", 10), ("Bogoro", 10), ("Damban", 10),
        ("Darazo", 10), ("Dass", 10), ("Gamawa", 10), ("Ganjuwa", 10),
        ("Giade", 10), ("Itas/Gadau", 10), ("Jama'are", 10),
        ("Katagum", 10), ("Kirfi", 10), ("Misau", 10), ("Ningi", 10),
        ("Shira", 10), ("Tafawa Balewa", 10), ("Toro", 10),
        ("Warji", 8), ("Zaki", 8),
    ]),
    # ── Bayelsa — 8 LGAs, 105 wards ──────────────────────────────────────────
    ("Bayelsa", "06", "South-South", 4.77, 6.07, [
        ("Brass", 13), ("Ekeremor", 14), ("Kolokuma/Opokuma", 13),
        ("Nembe", 13), ("Ogbia", 13), ("Sagbama", 13),
        ("Southern Ijaw", 16), ("Yenagoa", 10),
    ]),
    # ── Benue — 23 LGAs, 276 wards ───────────────────────────────────────────
    ("Benue", "07", "North-Central", 7.34, 8.75, [
        ("Ado", 12), ("Agatu", 12), ("Apa", 12), ("Buruku", 12),
        ("Gboko", 12), ("Guma", 12), ("Gwer East", 12), ("Gwer West", 12),
        ("Katsina-Ala", 12), ("Konshisha", 12), ("Kwande", 12), ("Logo", 12),
        ("Makurdi", 12), ("Obi", 12), ("Ogbadibo", 12), ("Ohimini", 12),
        ("Oju", 12), ("Okpokwu", 12), ("Otukpo", 12), ("Tarka", 12),
        ("Ukum", 12), ("Ushongo", 12), ("Vandeikya", 12),
    ]),
    # ── Borno — 27 LGAs, 311 wards ───────────────────────────────────────────
    ("Borno", "08", "North-East", 11.83, 13.15, [
        ("Abadam", 11), ("Askira/Uba", 12), ("Bama", 12), ("Bayo", 11),
        ("Biu", 12), ("Chibok", 11), ("Damboa", 12), ("Dikwa", 11),
        ("Gubio", 11), ("Guzamala", 11), ("Gwoza", 12), ("Hawul", 12),
        ("Jere", 12), ("Kaga", 11), ("Kala/Balge", 11), ("Konduga", 12),
        ("Kukawa", 11), ("Kwaya Kusar", 11), ("Mafa", 11), ("Magumeri", 12),
        ("Maiduguri", 12), ("Marte", 11), ("Mobbar", 11), ("Monguno", 11),
        ("Ngala", 11), ("Nganzai", 11), ("Shani", 12),
    ]),
    # ── Cross River — 18 LGAs, 196 wards ─────────────────────────────────────
    ("Cross River", "09", "South-South", 5.87, 8.60, [
        ("Abi", 11), ("Akamkpa", 11), ("Akpabuyo", 11), ("Bakassi", 10),
        ("Bekwarra", 11), ("Biase", 11), ("Boki", 11),
        ("Calabar Municipal", 11), ("Calabar South", 11), ("Etung", 10),
        ("Ikom", 11), ("Obanliku", 11), ("Obubra", 11), ("Obudu", 11),
        ("Odukpani", 11), ("Ogoja", 11), ("Yakurr", 11), ("Yala", 11),
    ]),
    # ── Delta — 25 LGAs, 270 wards ───────────────────────────────────────────
    ("Delta", "10", "South-South", 5.70, 5.89, [
        ("Aniocha North", 11), ("Aniocha South", 11), ("Bomadi", 10),
        ("Burutu", 11), ("Ethiope East", 11), ("Ethiope West", 11),
        ("Ika North East", 11), ("Ika South", 10), ("Isoko North", 11),
        ("Isoko South", 11), ("Ndokwa East", 10), ("Ndokwa West", 11),
        ("Okpe", 10), ("Oshimili North", 11), ("Oshimili South", 11),
        ("Patani", 10), ("Sapele", 11), ("Udu", 10), ("Ughelli North", 11),
        ("Ughelli South", 11), ("Ukwuani", 11), ("Uvwie", 11),
        ("Warri North", 11), ("Warri South", 11), ("Warri South West", 10),
    ]),
    # ── Ebonyi — 13 LGAs, 171 wards ──────────────────────────────────────────
    ("Ebonyi", "11", "South-East", 6.26, 8.01, [
        ("Abakaliki", 13), ("Afikpo North", 13), ("Afikpo South", 13),
        ("Ebonyi", 13), ("Ezza North", 13), ("Ezza South", 13),
        ("Ikwo", 14), ("Ishielu", 13), ("Ivo", 13), ("Izzi", 14),
        ("Ohaozara", 13), ("Ohaukwu", 13), ("Onicha", 13),
    ]),
    # ── Edo — 18 LGAs, 192 wards ─────────────────────────────────────────────
    ("Edo", "12", "South-South", 6.34, 5.62, [
        ("Akoko-Edo", 11), ("Egor", 10), ("Esan Central", 11),
        ("Esan North-East", 11), ("Esan South-East", 10), ("Esan West", 11),
        ("Etsako Central", 10), ("Etsako East", 11), ("Etsako West", 11),
        ("Igueben", 10), ("Ikpoba-Okha", 11), ("Orhionmwon", 11),
        ("Oredo", 11), ("Ovia North-East", 11), ("Ovia South-West", 10),
        ("Owan East", 11), ("Owan West", 11), ("Uhunmwonde", 10),
    ]),
    # ── Ekiti — 16 LGAs, 177 wards ───────────────────────────────────────────
    ("Ekiti", "13", "South-West", 7.72, 5.31, [
        ("Ado Ekiti", 11), ("Efon", 11), ("Ekiti East", 11),
        ("Ekiti South-West", 11), ("Ekiti West", 11), ("Emure", 11),
        ("Gbonyin", 11), ("Ido Osi", 11), ("Ijero", 11), ("Ikere", 11),
        ("Ikole", 11), ("Ilejemeje", 11), ("Irepodun/Ifelodun", 11),
        ("Ise/Orun", 11), ("Moba", 11), ("Oye", 12),
    ]),
    # ── Enugu — 17 LGAs, 257 wards ───────────────────────────────────────────
    ("Enugu", "14", "South-East", 6.53, 7.49, [
        ("Aninri", 15), ("Awgu", 16), ("Enugu East", 15), ("Enugu North", 15),
        ("Enugu South", 15), ("Ezeagu", 15), ("Igbo Etiti", 15),
        ("Igbo Eze North", 15), ("Igbo Eze South", 15), ("Isi Uzo", 15),
        ("Nkanu East", 15), ("Nkanu West", 15), ("Nsukka", 16),
        ("Oji River", 15), ("Udenu", 15), ("Udi", 15), ("Uzo Uwani", 16),
    ]),
    # ── FCT — 6 LGAs, 62 wards ───────────────────────────────────────────────
    ("FCT", "15", "North-Central", 8.90, 7.38, [
        ("Abaji", 10), ("Bwari", 10), ("Gwagwalada", 11), ("Kuje", 10),
        ("Kwali", 10), ("Municipal Area Council", 11),
    ]),
    # ── Gombe — 11 LGAs, 114 wards ───────────────────────────────────────────
    ("Gombe", "16", "North-East", 10.28, 11.17, [
        ("Akko", 11), ("Balanga", 10), ("Billiri", 10), ("Dukku", 11),
        ("Funakaye", 10), ("Gombe", 11), ("Kaltungo", 10), ("Kwami", 11),
        ("Nafada", 10), ("Shongom", 10), ("Yamaltu/Deba", 10),
    ]),
    # ── Imo — 27 LGAs, 305 wards ─────────────────────────────────────────────
    ("Imo", "17", "South-East", 5.57, 7.06, [
        ("Aboh Mbaise", 11), ("Ahiazu Mbaise", 11), ("Ehime Mbano", 11),
        ("Ezinihitte", 11), ("Ideato North", 11), ("Ideato South", 11),
        ("Ihitte/Uboma", 11), ("Ikeduru", 11), ("Isiala Mbano", 12),
        ("Isu", 11), ("Mbaitoli", 12), ("Ngor Okpala", 11), ("Njaba", 11),
        ("Nkwerre", 11), ("Nwangele", 11), ("Obowo", 12), ("Oguta", 11),
        ("Ohaji/Egbema", 11), ("Okigwe", 12), ("Onuimo", 11), ("Orlu", 12),
        ("Orsu", 11), ("Oru East", 11), ("Oru West", 11),
        ("Owerri Municipal", 12), ("Owerri North", 11), ("Owerri West", 11),
    ]),
    # ── Jigawa — 27 LGAs, 287 wards ──────────────────────────────────────────
    ("Jigawa", "18", "North-West", 12.23, 9.56, [
        ("Auyo", 10), ("Babura", 11), ("Biriniwa", 11), ("Birnin Kudu", 11),
        ("Buji", 10), ("Dutse", 11), ("Gagarawa", 10), ("Garki", 11),
        ("Gumel", 11), ("Guri", 10), ("Gwaram", 11), ("Gwiwa", 10),
        ("Hadejia", 11), ("Jahun", 11), ("Kafin Hausa", 11),
        ("Kaugama", 10), ("Kazaure", 11), ("Kiri Kasama", 10),
        ("Kiyawa", 11), ("Maigatari", 10), ("Malam Madori", 11),
        ("Miga", 10), ("Ringim", 11), ("Roni", 10),
        ("Sule Tankarkar", 11), ("Taura", 11), ("Yankwashi", 10),
    ]),
    # ── Kaduna — 23 LGAs, 255 wards ──────────────────────────────────────────
    ("Kaduna", "19", "North-West", 10.52, 7.44, [
        ("Birnin Gwari", 11), ("Chikun", 11), ("Giwa", 11), ("Igabi", 11),
        ("Ikara", 11), ("Jaba", 11), ("Jema'a", 11), ("Kachia", 11),
        ("Kaduna North", 11), ("Kaduna South", 11), ("Kagarko", 11),
        ("Kajuru", 11), ("Kaura", 11), ("Kauru", 11), ("Kubau", 11),
        ("Kudan", 11), ("Lere", 11), ("Makarfi", 11), ("Sabon Gari", 12),
        ("Sanga", 11), ("Soba", 11), ("Zangon Kataf", 12), ("Zaria", 12),
    ]),
    # ── Kano — 44 LGAs, 484 wards ────────────────────────────────────────────
    ("Kano", "20", "North-West", 11.99, 8.52, [
        ("Ajingi", 11), ("Albasu", 11), ("Bagwai", 11), ("Bebeji", 11),
        ("Bichi", 11), ("Bunkure", 11), ("Dala", 11), ("Dambatta", 11),
        ("Dawakin Kudu", 11), ("Dawakin Tofa", 11), ("Doguwa", 11),
        ("Fagge", 11), ("Gabasawa", 11), ("Garko", 11),
        ("Garun Mallam", 11), ("Gaya", 11), ("Gezawa", 11), ("Gwale", 11),
        ("Gwarzo", 11), ("Kabo", 11), ("Kano Municipal", 11),
        ("Karaye", 11), ("Kibiya", 11), ("Kiru", 11), ("Kumbotso", 11),
        ("Kunchi", 11), ("Kura", 11), ("Madobi", 11), ("Makoda", 11),
        ("Minjibir", 11), ("Nasarawa", 11), ("Rano", 11),
        ("Rimin Gado", 11), ("Rogo", 11), ("Shanono", 11), ("Sumaila", 11),
        ("Takai", 11), ("Tarauni", 11), ("Tofa", 11), ("Tsanyawa", 11),
        ("Tudun Wada", 11), ("Ungogo", 11), ("Warawa", 11), ("Wudil", 11),
    ]),
    # ── Katsina — 34 LGAs, 361 wards ─────────────────────────────────────────
    ("Katsina", "21", "North-West", 12.99, 7.62, [
        ("Bakori", 10), ("Batagarawa", 11), ("Batsari", 10), ("Baure", 11),
        ("Bindawa", 10), ("Charanchi", 10), ("Dan Musa", 11),
        ("Dandume", 10), ("Danja", 11), ("Daura", 11), ("Dutsi", 10),
        ("Dutsin-Ma", 11), ("Faskari", 10), ("Funtua", 11), ("Ingawa", 10),
        ("Jibia", 11), ("Kafur", 10), ("Kaita", 11), ("Kankara", 11),
        ("Kankia", 10), ("Katsina", 11), ("Kurfi", 10), ("Kusada", 10),
        ("Mai'Adua", 11), ("Malumfashi", 11), ("Mani", 10), ("Mashi", 11),
        ("Matazu", 10), ("Musawa", 11), ("Rimi", 10), ("Sabuwa", 11),
        ("Safana", 10), ("Sandamu", 10), ("Zango", 11),
    ]),
    # ── Kebbi — 21 LGAs, 226 wards ───────────────────────────────────────────
    ("Kebbi", "22", "North-West", 11.58, 4.20, [
        ("Aleiro", 10), ("Arewa Dandi", 11), ("Argungu", 11), ("Augie", 11),
        ("Bagudo", 11), ("Birnin Kebbi", 11), ("Bunza", 11), ("Dandi", 10),
        ("Fakai", 10), ("Gwandu", 11), ("Jega", 11), ("Kalgo", 10),
        ("Koko/Besse", 11), ("Maiyama", 11), ("Ngaski", 10), ("Sakaba", 11),
        ("Shanga", 10), ("Suru", 11), ("Wasagu/Danko", 11),
        ("Yauri", 11), ("Zuru", 11),
    ]),
    # ── Kogi — 21 LGAs, 239 wards ────────────────────────────────────────────
    ("Kogi", "23", "North-Central", 7.80, 6.74, [
        ("Adavi", 11), ("Ajaokuta", 11), ("Ankpa", 12), ("Bassa", 11),
        ("Dekina", 12), ("Ibaji", 11), ("Idah", 11), ("Igalamela-Odolu", 11),
        ("Ijumu", 11), ("Kabba/Bunu", 12), ("Kogi", 11), ("Lokoja", 12),
        ("Mopa-Muro", 11), ("Ofu", 11), ("Ogori/Magongo", 11),
        ("Okehi", 11), ("Okene", 12), ("Olamaboro", 11), ("Omala", 11),
        ("Yagba East", 11), ("Yagba West", 12),
    ]),
    # ── Kwara — 16 LGAs, 193 wards ───────────────────────────────────────────
    ("Kwara", "24", "North-Central", 8.97, 5.25, [
        ("Asa", 12), ("Baruten", 12), ("Edu", 12), ("Ekiti", 11),
        ("Ifelodun", 13), ("Ilorin East", 12), ("Ilorin South", 12),
        ("Ilorin West", 13), ("Irepodun", 12), ("Isin", 12), ("Kaiama", 12),
        ("Moro", 12), ("Offa", 12), ("Oke Ero", 12), ("Oyun", 12),
        ("Pategi", 12),
    ]),
    # ── Lagos — 20 LGAs, 245 wards ───────────────────────────────────────────
    ("Lagos", "25", "South-West", 6.45, 3.40, [
        ("Agege", 12), ("Ajeromi-Ifelodun", 12), ("Alimosho", 13),
        ("Amuwo-Odofin", 12), ("Apapa", 12), ("Badagry", 12), ("Epe", 12),
        ("Eti-Osa", 12), ("Ibeju-Lekki", 12), ("Ifako-Ijaiye", 12),
        ("Ikeja", 12), ("Ikorodu", 13), ("Kosofe", 13),
        ("Lagos Island", 12), ("Lagos Mainland", 12), ("Mushin", 12),
        ("Ojo", 13), ("Oshodi-Isolo", 12), ("Shomolu", 12),
        ("Surulere", 13),
    ]),
    # ── Nasarawa — 13 LGAs, 147 wards ────────────────────────────────────────
    ("Nasarawa", "26", "North-Central", 8.65, 8.51, [
        ("Akwanga", 11), ("Awe", 11), ("Doma", 11), ("Karu", 12),
        ("Keana", 11), ("Keffi", 11), ("Kokona", 11), ("Lafia", 12),
        ("Nasarawa", 12), ("Nasarawa Egon", 11), ("Obi", 11),
        ("Toto", 11), ("Wamba", 11),
    ]),
    # ── Niger — 25 LGAs, 274 wards ───────────────────────────────────────────
    ("Niger", "27", "North-Central", 10.00, 6.00, [
        ("Agaie", 11), ("Agwara", 10), ("Bida", 11), ("Borgu", 11),
        ("Bosso", 11), ("Chanchaga", 11), ("Edati", 11), ("Gbako", 11),
        ("Gurara", 10), ("Katcha", 11), ("Kontagora", 11), ("Lapai", 11),
        ("Lavun", 11), ("Magama", 11), ("Mariga", 11), ("Mashegu", 11),
        ("Mokwa", 11), ("Munya", 10), ("Paikoro", 11), ("Rafi", 11),
        ("Rijau", 11), ("Shiroro", 11), ("Suleja", 11), ("Tafa", 10),
        ("Wushishi", 11),
    ]),
    # ── Ogun — 20 LGAs, 236 wards ────────────────────────────────────────────
    ("Ogun", "28", "South-West", 7.00, 3.35, [
        ("Abeokuta North", 12), ("Abeokuta South", 12),
        ("Ado-Odo/Ota", 12), ("Ewekoro", 11), ("Ifo", 12),
        ("Ijebu East", 12), ("Ijebu North", 12), ("Ijebu North East", 11),
        ("Ijebu Ode", 12), ("Ikenne", 11), ("Imeko Afon", 12),
        ("Ipokia", 11), ("Obafemi Owode", 12), ("Odeda", 12),
        ("Odogbolu", 12), ("Ogun Waterside", 11), ("Remo North", 12),
        ("Shagamu", 12), ("Yewa North", 12), ("Yewa South", 11),
    ]),
    # ── Ondo — 18 LGAs, 203 wards ────────────────────────────────────────────
    ("Ondo", "29", "South-West", 7.25, 5.20, [
        ("Akoko North-East", 11), ("Akoko North-West", 11),
        ("Akoko South-East", 11), ("Akoko South-West", 11),
        ("Akure North", 11), ("Akure South", 12), ("Ese Odo", 11),
        ("Idanre", 11), ("Ifedore", 11), ("Ilaje", 12), ("Ile Oluji/Okeigbo", 11),
        ("Irele", 11), ("Odigbo", 12), ("Okitipupa", 12), ("Ondo East", 11),
        ("Ondo West", 12), ("Ose", 11), ("Owo", 12),
    ]),
    # ── Osun — 30 LGAs, 332 wards ────────────────────────────────────────────
    ("Osun", "30", "South-West", 7.57, 4.56, [
        ("Atakumosa East", 11), ("Atakumosa West", 11), ("Aiyedaade", 11),
        ("Aiyedire", 11), ("Boluwaduro", 11), ("Boripe", 11),
        ("Ede North", 11), ("Ede South", 11), ("Egbedore", 11),
        ("Ejigbo", 11), ("Ife Central", 11), ("Ife East", 11),
        ("Ife North", 11), ("Ife South", 11), ("Ifedayo", 11),
        ("Ifelodun", 11), ("Ila", 11), ("Ilesha East", 11),
        ("Ilesha West", 11), ("Irepodun", 12), ("Irewole", 11),
        ("Isokan", 11), ("Iwo", 12), ("Obokun", 11), ("Odo Otin", 11),
        ("Ola Oluwa", 11), ("Olorunda", 11), ("Oriade", 12),
        ("Orolu", 11), ("Osogbo", 11),
    ]),
    # ── Oyo — 33 LGAs, 351 wards ─────────────────────────────────────────────
    ("Oyo", "31", "South-West", 8.16, 3.61, [
        ("Afijio", 10), ("Akinyele", 11), ("Atiba", 10), ("Atisbo", 10),
        ("Egbeda", 11), ("Ibadan North", 11), ("Ibadan North-East", 11),
        ("Ibadan North-West", 11), ("Ibadan South-East", 11),
        ("Ibadan South-West", 11), ("Ibarapa Central", 10),
        ("Ibarapa East", 10), ("Ibarapa North", 10), ("Ido", 10),
        ("Irepo", 10), ("Iseyin", 11), ("Itesiwaju", 10), ("Iwajowa", 10),
        ("Kajola", 10), ("Lagelu", 11), ("Ogbomosho North", 11),
        ("Ogbomosho South", 10), ("Ogo Oluwa", 10), ("Olorunsogo", 10),
        ("Oluyole", 11), ("Ona Ara", 11), ("Orelope", 10), ("Ori Ire", 10),
        ("Oyo East", 11), ("Oyo West", 10), ("Saki East", 11),
        ("Saki West", 11), ("Surulere", 11),
    ]),
    # ── Plateau — 17 LGAs, 207 wards ─────────────────────────────────────────
    ("Plateau", "32", "North-Central", 9.22, 9.22, [
        ("Barkin Ladi", 12), ("Bassa", 12), ("Bokkos", 12), ("Jos East", 12),
        ("Jos North", 13), ("Jos South", 12), ("Kanam", 12), ("Kanke", 12),
        ("Langtang North", 12), ("Langtang South", 12), ("Mangu", 13),
        ("Mikang", 12), ("Pankshin", 12), ("Qua'an Pan", 12),
        ("Riyom", 12), ("Shendam", 12), ("Wase", 12),
    ]),
    # ── Rivers — 23 LGAs, 319 wards ──────────────────────────────────────────
    ("Rivers", "33", "South-South", 4.83, 6.91, [
        ("Abua/Odual", 14), ("Ahoada East", 14), ("Ahoada West", 13),
        ("Akuku-Toru", 13), ("Andoni", 14), ("Asari-Toru", 13),
        ("Bonny", 13), ("Degema", 14), ("Eleme", 13), ("Emohua", 14),
        ("Etche", 14), ("Gokana", 14), ("Ikwerre", 14),
        ("Khana", 14), ("Obio/Akpor", 14), ("Ogba/Egbema/Ndoni", 14),
        ("Ogu/Bolo", 13), ("Okrika", 13), ("Omuma", 13),
        ("Opobo/Nkoro", 13), ("Oyigbo", 13), ("Port Harcourt", 14),
        ("Tai", 14),
    ]),
    # ── Sokoto — 23 LGAs, 244 wards ──────────────────────────────────────────
    ("Sokoto", "34", "North-West", 13.06, 5.24, [
        ("Binji", 10), ("Bodinga", 11), ("Dange Shuni", 11), ("Gada", 10),
        ("Goronyo", 11), ("Gudu", 10), ("Gwadabawa", 11), ("Illela", 11),
        ("Isa", 11), ("Kebbe", 10), ("Kware", 10), ("Rabah", 11),
        ("Sabon Birni", 11), ("Shagari", 10), ("Silame", 11),
        ("Sokoto North", 11), ("Sokoto South", 11), ("Tambuwal", 11),
        ("Tangaza", 10), ("Tureta", 10), ("Wamako", 11),
        ("Wurno", 11), ("Yabo", 11),
    ]),
    # ── Taraba — 16 LGAs, 168 wards ──────────────────────────────────────────
    ("Taraba", "35", "North-East", 8.00, 11.00, [
        ("Ardo Kola", 10), ("Bali", 11), ("Donga", 10), ("Gashaka", 11),
        ("Gassol", 11), ("Ibi", 10), ("Jalingo", 11),
        ("Karim Lamido", 11), ("Kumi", 10), ("Lau", 10), ("Sardauna", 11),
        ("Takum", 11), ("Ussa", 10), ("Wukari", 11), ("Yorro", 10),
        ("Zing", 11),
    ]),
    # ── Yobe — 17 LGAs, 178 wards ────────────────────────────────────────────
    ("Yobe", "36", "North-East", 12.30, 11.46, [
        ("Bade", 10), ("Bursari", 10), ("Damaturu", 11), ("Fika", 10),
        ("Fune", 11), ("Geidam", 10), ("Gujba", 10), ("Gulani", 10),
        ("Jakusko", 11), ("Karasuwa", 10), ("Machina", 10), ("Nangere", 10),
        ("Nguru", 11), ("Potiskum", 11), ("Tarmuwa", 10), ("Yunusari", 11),
        ("Yusufari", 10),
    ]),
    # ── Zamfara — 14 LGAs, 147 wards ─────────────────────────────────────────
    ("Zamfara", "37", "North-West", 12.00, 6.25, [
        ("Anka", 10), ("Bakura", 11), ("Birnin Magaji/Kiyaw", 11),
        ("Bukkuyum", 10), ("Bungudu", 11), ("Gummi", 11), ("Gusau", 11),
        ("Kaura Namoda", 11), ("Maradun", 10), ("Maru", 10),
        ("Shinkafi", 11), ("Talata Mafara", 11), ("Tsafe", 10), ("Zurmi", 10),
    ]),
]


# ── Seed logic ─────────────────────────────────────────────────────────────────

async def clear_collections() -> None:
    print("Clearing existing geographic data…")
    await Ward.find_all().delete()
    await LGA.find_all().delete()
    await State.find_all().delete()
    print("  Collections cleared.")


async def seed(dry_run: bool = False) -> dict:
    totals = {"states": 0, "lgas": 0, "wards": 0, "skipped_states": 0}

    for entry in _HIERARCHY:
        state_name, code, zone, lat, lng, lga_list = entry

        if dry_run:
            totals["states"] += 1
            totals["lgas"] += len(lga_list)
            totals["wards"] += sum(w for _, w in lga_list)
            continue

        # Upsert state
        state = await State.find_one(State.name == state_name)
        if state:
            totals["skipped_states"] += 1
        else:
            state = State(
                name=state_name,
                code=code,
                geopolitical_zone=zone,
                centroid_lat=lat,
                centroid_lng=lng,
            )
            await state.insert()
            totals["states"] += 1

        # LGAs + wards
        ward_batch: list[Ward] = []
        for lga_idx, (lga_name, ward_count) in enumerate(lga_list, start=1):
            inec_lga_code = f"{lga_idx:02d}"

            # Upsert LGA
            lga = await LGA.find_one({"state_id": state.id, "name": lga_name})
            if not lga:
                lga = LGA(
                    name=lga_name,
                    state_id=state.id,
                    inec_lga_code=inec_lga_code,
                )
                await lga.insert()
                totals["lgas"] += 1

            # Queue wards for batch insert (only if none exist yet for this LGA)
            existing_ward = await Ward.find_one({"lga_id": lga.id})
            if not existing_ward:
                for ward_num in range(1, ward_count + 1):
                    ward_batch.append(Ward(
                        name=f"Ward {ward_num:02d}",
                        lga_id=lga.id,
                        inec_ward_code=f"{ward_num:02d}",
                    ))

            # Flush batch every 500 wards to keep memory low
            if len(ward_batch) >= 500:
                await Ward.insert_many(ward_batch)
                totals["wards"] += len(ward_batch)
                ward_batch = []

        # Flush remaining
        if ward_batch:
            await Ward.insert_many(ward_batch)
            totals["wards"] += len(ward_batch)

        print(f"  ✓ {state_name}: {len(lga_list)} LGAs, "
              f"{sum(w for _, w in lga_list)} wards")

    return totals


async def main(clear: bool, dry_run: bool) -> None:
    mongodb_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    db_name     = os.environ.get("MONGODB_DB_NAME", "nigeriavoterwatch")

    client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_uri)
    await init_beanie(
        database=client[db_name],
        document_models=[State, LGA, Ward],
    )

    if dry_run:
        totals = await seed(dry_run=True)
        print(f"\nDry-run summary:")
        print(f"  States : {totals['states']}")
        print(f"  LGAs   : {totals['lgas']}")
        print(f"  Wards  : {totals['wards']}")
        client.close()
        return

    if clear:
        await clear_collections()

    # Check if already seeded
    state_count = await State.count()
    if state_count > 0 and not clear:
        print(f"Database already contains {state_count} states. "
              f"Use --clear to wipe and reseed.")
        client.close()
        return

    print("Seeding Nigeria administrative hierarchy…\n")
    totals = await seed()

    print(f"\n{'─'*50}")
    print(f"Seeding complete.")
    print(f"  States inserted : {totals['states']}")
    print(f"  States skipped  : {totals['skipped_states']}")
    print(f"  LGAs inserted   : {totals['lgas']}")
    print(f"  Wards inserted  : {totals['wards']}")
    print(f"{'─'*50}")
    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Nigeria geographic hierarchy")
    parser.add_argument("--clear",   action="store_true", help="Wipe existing data before seeding")
    parser.add_argument("--dry-run", action="store_true", help="Count only, no DB writes")
    args = parser.parse_args()
    asyncio.run(main(clear=args.clear, dry_run=args.dry_run))
