# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Phạm Cường Quốc  **MSSV:** 2A202602469  **Ngày:** 2026-10-05

> Kỳ vọng và thang điểm: `SUBMISSION.md`. Mọi số liệu phải khớp với `ket_qua_benchmark_kg.txt`. Bản thiết kế ontology nộp riêng ở `report/ONTOLOGY.md`.

**Cấu hình chạy.** Chat `gemini:gemini-3.5-flash-lite`, embedding `gemini:gemini-embedding-001`, top_k=3, chunk_size=800, 176 chunk. Lý do không dùng OpenAI: key OpenAI trả 401. Model mặc định `gemini-2.5-flash-lite` trả 404 ("no longer available to new users"), nên đổi model bằng `GEMINI_CHAT_MODEL`. Lệnh chạy:

```
LLM_PROVIDER=gemini EMBEDDING_PROVIDER=gemini GEMINI_CHAT_MODEL=gemini-3.5-flash-lite python bench_kg.py --judge
LLM_PROVIDER=gemini EMBEDDING_PROVIDER=gemini GEMINI_CHAT_MODEL=gemini-3.5-flash-lite KG_ONTOLOGY=hint python bench_kg.py --judge --out ket_qua_benchmark_kg.hint.txt
```

Ba lưu ý về phép đo, áp dụng cho mọi bảng bên dưới:
1. **Giá USD:** `src/llm.py` chưa có giá `gemini-3.5-flash-lite`. Mình thêm giá paid tier từ trang giá Google: $0,30 / $2,50 cho 1M token vào/ra. Thực tế key này ở free tier nên chi phí thật bằng 0; USD trong báo cáo là "nếu trả phí".
2. **Embedding:** API OpenAI-compatible của Gemini không trả `usage` cho embedding, và trang giá không ghi giá `gemini-embedding-001`. Vì vậy dòng `flat` của Indexing có `in_tok = 0`, `USD = 0`. Chi phí embedding có thật nhưng không đo được; nó giống nhau ở cả 2 pipeline (Graph dùng lại cùng vector index).
3. **Độ trễ:** free tier giới hạn 15 request/phút. Mình thêm retry khi gặp 429 vào `src/llm.py`, nên thời gian chờ nằm trong `seconds`. Lần chạy chính có **một** lần chờ 25s, rơi vào câu Q2-graph (30,04s). Bỏ 25s đó thì độ trễ trung bình của Graph là (7,26 × 6 − 25) / 6 ≈ **3,09s/câu**.

## 1. Chi phí (10 điểm)

Dán 2 bảng `Indexing` và `Querying` từ `ket_qua_benchmark_kg.txt`:

```
Chat model: gemini:gemini-3.5-flash-lite | Embedding: gemini:gemini-embedding-001 | top_k=3 | chunk_size=800 | chunks=176 | KG: 203 nodes / 495 rels

== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176         0        0   0.00000    104.7
graph       196     37939     6662   0.02804    147.9

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.51   1.33      696       77   0.00040     1.73
graph       1.00   2.00     4555      139   0.00171     7.26
```

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | --- | --- | --- |
| Indexing USD | 0.00000 (embedding không đo được) | 0.02804 | không tính được (chia 0); phần tăng thêm là +$0,028 cho 20 lần gọi LLM trích xuất |
| Indexing giây | 104.7 | 147.9 | ×1,4 |
| Mỗi câu: USD | 0.00040 | 0.00171 | ×4,3 |
| Mỗi câu: giây | 1.73 | 7.26 (≈ 3,09 nếu bỏ 25s chờ 429) | ×4,2 (≈ ×1,8 nếu bỏ thời gian chờ) |
| Mỗi câu: in_tok | 696 | 4555 | ×6,5 |

**Chi phí tăng thêm đến từ đâu?**
> **Lúc dựng hệ thống:** toàn bộ phần tăng thêm là 20 lần gọi LLM trích xuất JSON từ 20 bài báo (37.939 token vào, 6.662 ra, $0,028, khoảng 43s). Phần luật trích bằng regex nên không tốn đồng nào.
>
> **Lúc trả lời:** câu hỏi đắt hơn ×4,3 gần như hoàn toàn do prompt dài hơn (in_tok ×6,5). Mỗi câu Graph thêm khoảng 3.900 token dữ kiện: tóm tắt tối đa 10 vụ, thang khung hình phạt, khoản 1, dòng `FALLS_UNDER`, và các cạnh 1 bước quanh seed. Output chỉ tăng từ 77 lên 139 token.
>
> **Điểm hòa vốn:** chi phí tăng thêm của Graph là $0,028 + $0,0013 × N câu. Với 1.000 câu thì khoảng $1,3. Ở bộ câu hỏi này, Flat sai một phần ở **4/6 câu** (judge 1), toàn bộ là các câu cần nối 2 KB hoặc tổng hợp. Nếu tỉ lệ câu xuyên KB tương tự, Graph đáng tiền ngay từ những câu đầu tiên. Nếu chỉ hỏi câu single-hop thì đây là ×4,3 tiền mà không đổi được gì: Q1, Q2 hai bên đều đạt judge 2.
>
> **So với ontology gợi ý** (`ket_qua_benchmark_kg.hint.txt`): ontology của mình tốn thêm 13,3% lúc indexing ($0,02804 so với $0,02475, do prompt trích xuất dài hơn). Nhưng mỗi câu hỏi rẻ hơn 11,9% ($0,00171 so với $0,00194; in_tok 4.555 so với 5.129), vì không đưa toàn văn mọi khoản nhắc tới chất mà chỉ đưa thang khung một dòng và đúng khoản `FALLS_UNDER`.

## 2. Từng câu hỏi (10 điểm)

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao (1 câu) |
| --- | --- | --- | --- | --- | --- |
| Q1 | single-hop-law | 1.00 / 2 | 1.00 / 2 | Hòa | Định nghĩa "tiền chất" nằm gọn trong 1 chunk của Điều 2 Luật PCMT; vector search là đủ. |
| Q2 | single-hop-news | 1.00 / 2 | 1.00 / 2 | Hòa | Tên 2 bị cáo tử hình nằm trong cùng đoạn văn với "36kg"; Graph chỉ tốn thêm token. |
| Q3 | cross-kb | 0.33 / 1 | 1.00 / 2 | Graph | Flat có "36 tháng" nhưng không có chunk Điều 251 ("Ngữ cảnh không đủ thông tin"); Graph đi Person → Case → Crime ← Article → khoản 1. |
| Q4 | cross-kb | 0.33 / 1 | 1.00 / 2 | Graph | Flat không có Điều 255; Graph đưa cả thang khung nên thấy khung cao nhất "20 năm hoặc tù chung thân" (bản gợi ý chỉ có khoản 1 nên trả lời sai "07 năm"). |
| Q5 | cross-kb-multi-hop | 0.40 / 1 | 1.00 / 2 | Graph | Cạnh `FALLS_UNDER` đã tính sẵn 9.600 g MDMA ≥ 100 g → Điều 250 khoản 4 điểm b; Flat không có Điều 250. |
| Q6 | aggregation | 0.00 / 1 | 1.00 / 2 | Graph | Top-3 chunk chỉ là 3 đoạn của cùng vụ Cái Quang Huy; Graph lấy mọi `Case -INVOLVES-> MDMA` trên cả 20 bài. |

**Quy luật:** loại câu hỏi quyết định bên thắng. Câu **single-hop**, khi đáp án nằm trong một đoạn văn, thì Flat = Graph (2/2 hòa) và Flat rẻ hơn ×4. Câu **cross-kb** hoặc **multi-hop** thì Flat luôn thiếu nửa đáp án nằm ở KB kia (recall 0,33–0,40), còn Graph đạt 1,00. Câu **aggregation** thì top-k làm Flat mù hoàn toàn với các bài ngoài top-3 (recall 0,00), Graph liệt kê đủ. Cả 4/4 câu Graph thắng đều là câu mà đáp án **không có trong một đoạn văn duy nhất**.

## 3. Phân tích lỗi (20 điểm)

### Lỗi E2: Thiếu ngữ cảnh luật, sai khung hình phạt tối đa (ontology gợi ý)

- **Hiện tượng:** với ontology gợi ý, Q4 hỏi "phạt tù **tối đa** bao nhiêu". Graph trả lời "đến 07 năm tù". Điều 255 khoản 4 thực ra là "tù 20 năm hoặc tù chung thân".
- **Bằng chứng:** `ket_qua_benchmark_kg.hint.txt`, Q4 graph, recall=0.67, judge=1:

```
Đối với tội tổ chức sử dụng trái phép chất ma túy quy định tại Điều 255 BLHS ... (cụ thể tại khoản 1, mức phạt tù từ 02 năm đến 07 năm).
Ngữ cảnh không cung cấp các khoản nặng hơn của Điều 255, do đó mức phạt tù tối đa được nêu rõ trong ngữ cảnh là đến 07 năm tù
```

  Theo quy tắc lọc khoản của gợi ý (khoản 1 + khoản `MENTIONS` một chất mà vụ `INVOLVES`), Điều 255 không có khoản nào nhắc tên chất, nên chỉ còn khoản 1:

```cypher
MATCH (a:Article {id:'Điều 255 BLHS'})-[:HAS_CLAUSE]->(cl)-[:MENTIONS]->(s) RETURN cl.number, s.name
```
```
(no records)   // Điều 255 không định lượng theo chất → quy tắc lọc chỉ giữ khoản 1
```

- **Nguyên nhân:** do **Cypher của KG-3 và thiết kế ontology**. Quy tắc lọc ngầm giả định "khoản nặng = khoản có khối lượng chất lớn". Giả định này đúng với Điều 248–252, nhưng sai với các tội không định lượng (Điều 255–258), nơi khoản nặng dựa trên tình tiết như "gây chết người", "đối với 02 người trở lên".
- **Đề xuất sửa (đã làm trong ontology của mình):** mỗi Điều luật đi qua cầu nối được đưa thêm **một dòng thang khung**, gồm mọi `Clause.penalty`, khoảng 60 token. Kết quả trong `ket_qua_benchmark_kg.txt`, Q4 graph, recall=1.00, judge=2: "mức hình phạt tù cao nhất đối với khoản 4 của tội danh này là **20 năm hoặc tù chung thân**". Đánh đổi: khoảng 60 token mỗi Điều. Tổng in_tok mỗi câu vẫn giảm so với gợi ý (4.555 so với 5.129), vì đã bỏ toàn văn các khoản `MENTIONS`.

### Lỗi E3: Trùng thực thể, một vụ án thành nhiều node `Case`

- **Hiện tượng:** câu trả lời Q6 liệt kê cùng một vụ Cái Quang Huy 2 lần. Trong graph, một người ngoài đời bị nối với nhiều `Case` cùng nội dung.
- **Bằng chứng:** ontology gợi ý, Q6 graph:

```
1. Vụ vận chuyển hơn 10kg ma túy từ Đức về Việt Nam qua sân bay Nội Bài (Cái Quang Huy ... hơn 9,6kg MDMA và gần 406g Ketamine).
...
3. Vụ vận chuyển trái phép chất ma túy qua sân bay Nội Bài liên quan đến Cái Quang Huy (... hơn 9,6kg MDMA và gần 406g Ketamine ...).
```

  Ontology của mình cũng vậy (Q6 graph, mục 1: "Vụ ... do Cái Quang Huy chủ mưu (hoặc *Vụ ... liên quan đến Cái Quang Huy*)"). Truy vấn trên graph:

```cypher
MATCH (p:Person {name:'Cái Quang Huy'})-[:INVOLVED_IN]->(k) RETURN k.id, k.name;
MATCH (p:Person)-[:INVOLVED_IN]->(k:Case) WITH p, count(k) AS n WHERE n > 1 RETURN p.name, n;
```
```
news-100260918080821054#1 | Vụ vận chuyển ma túy qua sân bay Nội Bài liên quan đến Cái Quang Huy
news-100260917203001265#0 | Vụ vận chuyển ma túy qua sân bay Nội Bài do Cái Quang Huy chủ mưu

Cái Quang Huy 2 | Nguyễn Minh Đức 2 | Dương Minh Tuấn 4 | Phan Kim Nhi 3 | Nguyễn Thị Mai Anh 3 | Lê Văn Đông 3 | Trần Quốc An 2
```

- **Nguyên nhân:** do **khóa định danh trong thiết kế ontology**. Ontology gợi ý khóa `Case.name` theo tên LLM tự đặt, nên hai bài viết về cùng vụ đặt hai tên khác nhau và `MERGE` không gộp được. Ontology của mình chủ động khóa `Case.id = doc_id#i` để **không bao giờ gộp nhầm** (đánh đổi đã ghi ở ONTOLOGY.md, quyết định 2), nhưng vì thế cũng không gộp đúng. Riêng `Person` thì gộp được nhờ chuẩn hóa tên: Dương Minh Tuấn là 1 node nối 4 `Case`. Chính node `Person` này là dấu hiệu nhận ra các `Case` trùng.
- **Đề xuất sửa:** thêm một bước hậu xử lý sau build. Hai `Case` có chung ≥ 1 `Person` bị cáo/bị can, chung `Crime`, và `INVOLVES` cùng chất với `amount_g` lệch dưới 5% thì nối bằng `(:Case)-[:SAME_AS]->(:Case)`, hoặc gộp bằng `apoc.refactor.mergeNodes`. Lúc trả lời, `context()` chỉ lấy một đại diện mỗi cụm. Đánh đổi: thêm 1 truy vấn Cypher lúc build, không tốn token. Rủi ro là gộp nhầm hai vụ khác nhau của cùng một người, nên điều kiện phải chặt (cùng chất, cùng khối lượng).

### Lỗi E6 (mở rộng): Thuộc tính sai hoặc thiếu do LLM trích xuất, lan sang câu trả lời và cạnh suy ra

- **Hiện tượng:**
  1. Câu trả lời Q4 gọi Hoàng Nato là "Dương Minh Tuấn / Lê Văn Đông". Lê Văn Đông là bị cáo trốn Viện Pháp y tâm thần đi Sầm Sơn, không phải Hoàng Nato.
  2. Vụ Hoàng Nato bị xếp vào "khoản 4" của Điều 249 và 251, dù bài báo chỉ nói "khoảng 100g ma túy tổng hợp các loại".
  3. Nhiều cạnh `INVOLVED_IN` có `charge` rỗng.
- **Bằng chứng:**

```cypher
MATCH (p:Person) WHERE 'Hoàng Nato' IN p.aliases RETURN p.name, p.aliases;
```
```
Dương Minh Tuấn | ['Hoàng Nato']
Lê Văn Đông     | ['Hoàng Nato']      ← sai
```
```
$ grep -l "Lê Văn Đông" data/drug_news/*.md | xargs grep -c "Nato"
news-100260924105118645.md:0
news-100260930085028036.md:0            ← cả 2 bài về Lê Văn Đông không hề nhắc "Nato"
```
```cypher
MATCH (k:Case)-[f:FALLS_UNDER]->(c:Clause) WHERE k.name CONTAINS 'Nato' RETURN k.name, f.substance, f.amount_g, c.id;
```
```
Vụ bắt giữ giang hồ Hoàng Nato ... | Methamphetamine | 100.0 | Điều 251 BLHS khoản 4
Vụ bắt giữ giang hồ Hoàng Nato ... | Methamphetamine | 100.0 | Điều 249 BLHS khoản 4
```
  Bài gốc (news-100260920221957595): "…khoảng 100g ma túy tổng hợp các loại…". Bài không nói đó là Methamphetamine. Vụ 36kg cũng tương tự: bài viết "hơn 36kg ma túy các loại", nhưng graph có `INVOLVES {amount_g: 36000}` → Methamphetamine → Điều 251 khoản 4.

```cypher
MATCH (p:Person)-[r:INVOLVED_IN]->(k) WHERE r.charge = '' RETURN p.name, r.role, k.name;
```
```
22 dòng, ví dụ:
Ngô Văn Vinh   | cán bộ  | Vụ án xảy ra tại Viện Pháp y tâm thần ...   ← hợp lý: cán bộ bị xử về tội chức vụ, không phải tội ma túy
Touch Poleak   | cán bộ  | Vụ vận chuyển ... tại Preah Sihanouk         ← hợp lý: người phát ngôn của cảnh sát
Lê Văn Đông    | bị cáo  | Vụ tàng trữ trái phép chất ma túy tại Viện… ← LỖI: tên vụ là "tàng trữ" mà charge của bị cáo rỗng
```

- **Nguyên nhân:** do **prompt trích xuất cộng với việc tin dữ liệu LLM ở bước sau**.
  1. Prompt yêu cầu biệt danh nhưng không yêu cầu biệt danh phải xuất hiện nguyên văn trong bài, nên LLM "điền cho đủ" bằng một biệt danh nổi bật trong cùng chủ đề.
  2. Chuỗi "ma túy tổng hợp các loại" không có chất cụ thể, nhưng prompt bắt dùng tên chuẩn "nếu khớp", nên LLM ép về Methamphetamine. Sau đó `FALLS_UNDER` (do chính mình thiết kế) **khuếch đại** lỗi này thành một dữ kiện pháp lý trông rất chắc chắn.
  3. `charge` rỗng: với cán bộ thì hợp lý (tội của họ không thuộc Chương XX). Với bị cáo thì là lỗi trích xuất, khi LLM chỉ điền tội ở mức vụ (`charges`) mà bỏ mức người.
- **Đề xuất sửa** (trong `src/graph.py`, `extract_news_cases_own`):
  1. Kiểm chứng trong code: bỏ alias và tên chất nào không xuất hiện nguyên văn (hoặc qua bảng đồng nghĩa) trong `doc.content`. Rẻ, không tốn token.
  2. Với chất chung chung ("ma túy tổng hợp", "các loại"), map về node `Chất ma túy chưa xác định` và **không** tạo `FALLS_UNDER`.
  3. `charge` rỗng của bị cáo/bị can thì kế thừa từ `charges` của vụ khi vụ chỉ có 1 tội.

  Đánh đổi: (1) và (3) có thể bỏ sót biệt danh viết khác dấu; (2) làm Q6 mất các vụ có MDMA nằm trong "các loại".

### Ghi nhận thêm (không tính điểm)

- **E1 (cầu gãy):** `MATCH (k:Case) WHERE NOT (k)-[:CHARGED_WITH]->() RETURN k.name, k.stage` trả 3 vụ, cả 3 đều có `stage = 'bắt giữ'` ("Vụ bắt giữ Nguyễn Minh Đức", "Chuyên án A3-626p", "Vụ … tại Preah Sihanouk" ở Campuchia). Ở giai đoạn bắt giữ, báo chưa nêu tội danh, nên cầu gãy là **đúng dữ liệu**, không phải lỗi. Thuộc tính `stage` mới thêm giúp phân biệt trường hợp này với cầu gãy do `link_entity`.
- **E4 (phép đo):** Flat Q6 có recall = 0.00 nhưng judge = 1. Câu trả lời liệt kê "3 vụ việc" mà thực ra chỉ là 3 đoạn của **cùng** vụ Cái Quang Huy, và không nêu được tên vụ nào trong đáp án. Judge vẫn cho điểm "đúng một phần" vì thấy chữ MDMA. Ở đây recall đúng hơn judge. Ngược lại, recall dễ bị đánh lừa bởi chuỗi đúng nằm trong câu sai: bản gợi ý Q4 graph có recall 0.67 dù kết luận "tối đa 07 năm" là sai.

## 4. Kết luận (5 điểm)

Khi nào nên dùng KG, khi nào Flat RAG là đủ? Dẫn số liệu ở mục 1–2.
> **Flat RAG là đủ** khi đáp án nằm trong một đoạn văn. Q1 (luật) và Q2 (tin) cho thấy Flat đạt judge 2/2 như Graph, với chi phí $0,00040/câu so với $0,00171 (×4,3) và 696 token vào so với 4.555.
>
> **Nên dùng KG** khi câu hỏi phải **nối 2 nguồn** (Q3, Q4: Flat recall 0,33, Graph 1,00), **đi nhiều bước có phép so sánh** (Q5: so khối lượng với ngưỡng luật, Flat 0,40, Graph 1,00), hoặc **tổng hợp trên toàn kho** vượt quá top-k (Q6: Flat 0,00, Graph 1,00). Trên 4 câu loại này, judge trung bình là 1,00 (Flat) so với 2,00 (Graph).
>
> **Cái giá:** $0,028 và khoảng 43s dựng graph một lần (20 lần gọi LLM cho 20 bài), cộng thêm khoảng $0,0013 mỗi câu. KG đáng tiền khi (a) dữ liệu có **thực thể lặp lại giữa các nguồn** (tội danh, chất, người), (b) một phần dữ liệu **có cấu trúc** để trích bằng regex miễn phí, như luật ở đây, và (c) tỉ lệ câu xuyên nguồn hoặc tổng hợp đáng kể. Ở bộ này là 4/6.
>
> **Thiết kế ontology quan trọng ngang việc có KG hay không.** Cùng dữ liệu và cùng model, ontology gợi ý đạt judge 1,83, ontology của mình đạt 2,00 với chi phí mỗi câu thấp hơn 12%. Hai cải tiến làm nên khác biệt là thang khung hình phạt và cạnh `FALLS_UNDER`. Nhưng KG cũng biến lỗi trích xuất thành "sự thật" trông chắc chắn (mục 3, E6). Vì vậy dữ liệu LLM trích ra cần được kiểm chứng trong code trước khi suy luận tiếp trên nó.

## 5. Tự kiểm (5 điểm)

```
$ pytest tests/ -q
................................................                         [100%]
48 passed in 0.13s

$ LLM_PROVIDER=gemini EMBEDDING_PROVIDER=gemini GEMINI_CHAT_MODEL=gemini-3.5-flash-lite python bench_kg.py --check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = gemini:gemini-3.5-flash-lite | embedding = gemini:gemini-embedding-001
[OK] KG-2 build_graph: 150 node / 391 cạnh, đường xuyên 2 KB dài 1 cạnh
[OK] KG-3 context: 21 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00000. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

(`$0.00000` vì lúc chạy `--check`, bảng giá chưa có `gemini-3.5-flash-lite`; mình thêm giá trước khi chạy benchmark.)

Ảnh Neo4j: `report/img/kg_count.png`, `report/img/kg_cross_kb.png`, `report/img/kg_my_case.png`.
Người đã chọn cho `kg_my_case.png`: **Cái Quang Huy**

## Vấn đề gặp phải (không tính điểm)

- `OPENAI_API_KEY` trả `401 invalid_api_key`, nên chuyển sang Gemini bằng `LLM_PROVIDER=gemini EMBEDDING_PROVIDER=gemini` (biến môi trường, không sửa `.env`).
- `gemini-2.5-flash-lite` trả `404 … no longer available to new users`, nên dùng `GEMINI_CHAT_MODEL=gemini-3.5-flash-lite` và thêm giá của model này vào `PRICES_PER_M` trong `src/llm.py`.
- Free tier trả `429 RESOURCE_EXHAUSTED` (15 request/phút), nên thêm `MeteredLLM._retry` vào `src/llm.py`: đọc "retry in Xs" rồi ngủ và gọi lại. Thời gian chờ có nằm trong latency; xem lưu ý 3 ở đầu báo cáo.
