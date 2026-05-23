# 鏁版嵁搴撶粨鏋勮鏄?
## 2026-05-23：AI Prompt 方案

- `tasks` 表新增 `ai_prompt_preset_id`，记录当前任务使用哪一套 AI 分析 Prompt。
- 新增 `ai_prompt_presets` 表，用于保存全局共用的 1、2、3 号 Prompt 方案。
- 新增 `ai_analysis_runs` 表，用于保存每一次 AI 分析历史，支持刷新后继续展示分析预览和恢复旧分析结果。

### ai_prompt_presets 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | Prompt 方案 ID，例如 `preset_001` |
| `slot` | INTEGER | 方案编号，当前固定为 1、2、3 |
| `name` | TEXT | 用户可编辑的方案名称 |
| `prompt_text` | TEXT | 完整 AI 分析 Prompt |
| `is_default` | INTEGER | 是否默认方案 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |

### ai_analysis_runs 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 历史分析 ID |
| `task_id` | TEXT | 所属任务 ID |
| `run_number` | INTEGER | 第几次分析 |
| `provider` | TEXT | 实际使用的 AI 来源 |
| `provider_label` | TEXT | 页面展示用来源名称 |
| `model` | TEXT | 实际使用模型 |
| `ai_prompt_preset_id` | TEXT | 当次使用的 Prompt 方案 ID |
| `ai_prompt_preset_name` | TEXT | 当次使用的 Prompt 方案名称 |
| `requested_clip_count` | INTEGER | 当次请求输出的候选片段数量 |
| `clip_count` | INTEGER | 当次实际生成的候选片段数量 |
| `analysis_summary` | TEXT | 当次整体分析总结 |
| `fallback_notice` | TEXT | 远程降级本地等提示 |
| `analysis_payload_json` | TEXT | 完整 AI 分析结果 JSON |
| `created_at` | TEXT | 创建时间 |
褰撳墠鏁版嵁搴撲娇鐢?SQLite锛屾暟鎹簱鏂囦欢榛樿浣嶄簬锛?
```text
data/workflow.sqlite3
```

澶у瀷瑙嗛銆侀煶棰戙€佽浆鍐?Markdown 鍜屽悗缁緭鍑烘枃浠朵笉鏀捐繘鏁版嵁搴擄紝缁熶竴鏀惧湪锛?
```text
E:\鐩存挱闂村垏鐗囧伐浣滄祦瀛樺偍\{task_id}\
```

## tasks 琛?
`tasks` 琛ㄧ敤浜庝繚瀛樼洿鎾棰戝鐞嗕换鍔＄殑鍩虹淇℃伅鍜岀姸鎬併€?
| 瀛楁 | 绫诲瀷 | 璇存槑 |
| --- | --- | --- |
| `id` | TEXT | 浠诲姟鍞竴 ID锛屽垱寤烘椂鑷姩鐢熸垚 |
| `task_name` | TEXT | 浠诲姟鍚嶇О |
| `source_type` | TEXT | 瑙嗛鏉ユ簮锛歚upload` 鎴?`nas` |
| `platform` | TEXT | 骞冲彴绫诲瀷锛歚douyin`銆乣bilibili`銆乣general` |
| `original_video_path` | TEXT | 鏈湴涓婁紶瑙嗛璺緞锛屽悗缁帴鐪熷疄涓婁紶鍚庡啓鍏?|
| `nas_file_path` | TEXT | NAS / 鏈湴宸叉湁瑙嗛璺緞 |
| `max_clip_duration` | INTEGER | 鍗曟潯鍒囩墖鏈€闀挎椂闀匡紝鍗曚綅锛氬垎閽?|
| `candidate_clip_count` | INTEGER | 甯屾湜 AI 杈撳嚭鐨勫€欓€夌墖娈垫暟閲?|
| `ai_preference` | TEXT | AI 鐗囨閫夋嫨鍋忓ソ |
| `status` | TEXT | 褰撳墠浠诲姟鐘舵€?|
| `progress` | INTEGER | 褰撳墠杩涘害鐧惧垎姣旓紝鍚庣画娴佹按绾挎帹杩涙椂鏇存柊 |
| `error_message` | TEXT | 寮傚父淇℃伅 |
| `is_deleted` | INTEGER | 鏄惁宸蹭粠椤甸潰鍒楄〃闅愯棌锛宍1` 琛ㄧず闅愯棌锛屾枃浠朵笉浼氳鍒犻櫎 |
| `deleted_at` | TEXT | 闅愯棌鏃堕棿锛孖SO 鏍煎紡 |
| `created_at` | TEXT | 鍒涘缓鏃堕棿锛孖SO 鏍煎紡 |
| `updated_at` | TEXT | 鏇存柊鏃堕棿锛孖SO 鏍煎紡 |

## 浠诲姟鐘舵€佸€?
`status` 浣跨敤鑻辨枃鐘舵€佺爜淇濆瓨锛岄〉闈㈠睍绀烘椂鍐嶈浆鎹㈡垚涓枃銆?
| 鐘舵€佺爜 | 涓枃灞曠ず |
| --- | --- |
| `pending_video` | 寰呮彁浜よ棰?|
| `pending_processing` | 寰呭鐞?|
| `audio_extracting` | 闊抽鎻愬彇涓?|
| `transcribing` | 杞啓涓?|
| `pending_ai` | 寰?AI 鍒嗘瀽 |
| `ai_analyzing` | AI 鍒嗘瀽涓?|
| `pending_review` | 寰呬汉宸ュ鏍?|
| `cutting` | 鍒囧壊涓?|
| `completed` | 宸插畬鎴?|
| `completed_with_errors` | 閮ㄥ垎瀹屾垚锛岃嚦灏戞湁涓€涓垏鐗囨垚鍔燂紝浣嗕篃鏈夊垏鐗囧け璐?|
| `failed` | 澶辫触 |

## output_clip 琛?
`output_clip` 琛ㄧ敤浜庝繚瀛樻瘡涓€鏉℃渶缁堝垏鐗囪緭鍑虹粨鏋溿€傝棰戞枃浠舵湰韬粛淇濆瓨鍦ㄤ换鍔＄洰褰曢噷锛屾暟鎹簱鍙繚瀛樿矾寰勩€佺姸鎬佸拰閿欒淇℃伅銆?
| 瀛楁 | 绫诲瀷 | 璇存槑 |
| --- | --- | --- |
| `id` | TEXT | 杈撳嚭璁板綍鍞竴 ID |
| `task_id` | TEXT | 鎵€灞炰换鍔?ID |
| `clip_candidate_id` | TEXT | 鏉ユ簮鍊欓€夌墖娈?ID |
| `output_file_path` | TEXT | 杈撳嚭瑙嗛瀹屾暣璺緞 |
| `output_file_name` | TEXT | 杈撳嚭瑙嗛鏂囦欢鍚?|
| `status` | TEXT | 杈撳嚭鐘舵€侊細`pending`銆乣processing`銆乣completed`銆乣failed` |
| `error_message` | TEXT | 鍗曟潯鍒囩墖澶辫触鍘熷洜 |
| `created_at` | TEXT | 鍒涘缓鏃堕棿锛孖SO 鏍煎紡 |
| `updated_at` | TEXT | 鏇存柊鏃堕棿锛孖SO 鏍煎紡 |

## 鍏煎璇存槑

鏃╂湡椤圭洰楠ㄦ灦鏇句娇鐢ㄨ繃 `title`銆乣source_path`銆乣max_clip_minutes`銆乣target_clip_count` 绛夎崏妗堝瓧娈点€傚綋鍓嶅垵濮嬪寲閫昏緫浼氳嚜鍔ㄨˉ榻愭柊瀛楁锛屽苟鎶婃棫瀛楁鏁版嵁杩佺Щ鍒板綋鍓嶅瓧娈典腑銆?
涓轰簡涓嶇牬鍧忓凡鏈夋湰鍦版暟鎹簱锛屾棫瀛楁涓嶄細琚己鍒跺垹闄ゃ€傚悗缁唬鐮佷互鏈枃浠跺垪鍑虹殑褰撳墠瀛楁涓哄噯銆?
浠诲姟闅愯棌閲囩敤杞垹闄ゆ柟寮忥細`DELETE /api/tasks/{task_id}` 鍙細鎶?`is_deleted` 鏀逛负 `1` 骞跺啓鍏?`deleted_at`銆傚伐浣滃彴銆佷换鍔″垪琛ㄥ拰鐗囨瀹℃牳鎬昏榛樿涓嶆樉绀洪殣钘忎换鍔★紝浣?E 鐩樹换鍔＄洰褰曘€佸師瑙嗛銆侀煶棰戙€佽浆鍐欍€丄I 鍒嗘瀽鏂囦欢鍜屽垏鐗囪緭鍑洪兘浼氫繚鐣欍€?
`clip_candidates.reason` 鏄棭鏈熸帹鑽愮悊鐢卞瓧娈碉紝褰撳墠瀹℃牳椤典紭鍏堣鍙?`highlight_reason`銆傛暟鎹簱鍒濆鍖栨椂浼氭妸宸叉湁 `reason` 鑷姩琛ュ埌 `highlight_reason`銆?
## clip_candidates 琛?
`clip_candidates` 琛ㄧ敤浜庝繚瀛?AI 鍒嗘瀽鐢熸垚銆佺瓑寰呬汉宸ュ鏍哥殑鍊欓€夌煭瑙嗛鐗囨锛屼篃淇濆瓨浜哄伐瀹℃牳椤靛啓鍥炵殑缂栬緫缁撴灉銆?
| 瀛楁 | 绫诲瀷 | 璇存槑 |
| --- | --- | --- |
| `id` | TEXT | 鍊欓€夌墖娈垫暟鎹簱 ID |
| `task_id` | TEXT | 鎵€灞炰换鍔?ID |
| `clip_key` | TEXT | AI 杩斿洖鐨勭墖娈?key锛屼緥濡?`clip_001` |
| `title` | TEXT | 鐗囨鏍囬锛屽彲鍦ㄥ鏍搁〉浜哄伐淇敼 |
| `start_time` | TEXT | 寮€濮嬫椂闂达紝淇濆瓨涓?`HH:MM:SS` |
| `end_time` | TEXT | 缁撴潫鏃堕棿锛屼繚瀛樹负 `HH:MM:SS` |
| `duration_seconds` | INTEGER | 鐗囨鏃堕暱锛屽崟浣嶇锛屼繚瀛樺鏍镐慨鏀规椂鑷姩閲嶇畻 |
| `summary` | TEXT | 鐗囨鎽樿锛屽彲鍦ㄥ鏍搁〉浜哄伐淇敼 |
| `reason` | TEXT | 鍏煎鏃у瓧娈碉紝褰撳墠涓庢帹鑽愮悊鐢变繚鎸佷竴鑷?|
| `highlight_reason` | TEXT | AI 鎺ㄨ崘鐞嗙敱 |
| `spread_value` | TEXT | 浼犳挱浠峰€?|
| `suggested_editing` | TEXT | 鍓緫寤鸿 |
| `confidence_score` | REAL | AI 缃俊搴︼紝鑼冨洿 0 鍒?1 |
| `selected_by_default` | INTEGER | AI 鏄惁寤鸿榛樿鍚敤 |
| `enabled` | INTEGER | 浜哄伐瀹℃牳鏃舵槸鍚﹀惎鐢紝`1` 鍚敤锛宍0` 绂佺敤 |
| `reviewed` | INTEGER | 鏄惁宸蹭汉宸ヤ慨鏀规垨瀹℃牳锛屼繚瀛樺悗鍐欎负 `1` |
| `created_at` | TEXT | 鍒涘缓鏃堕棿锛孖SO 鏍煎紡 |
| `updated_at` | TEXT | 鏇存柊鏃堕棿锛孖SO 鏍煎紡 |

## 浠诲姟浜х墿璺緞

浠诲姟浜х墿璺緞褰撳墠鐢?`task_id` 鎺ㄥ锛屼笉棰濆鍐欏叆鏁版嵁搴擄細

| 浜х墿 | 璺緞 |
| --- | --- |
| 浠诲姟鐩綍 | `E:\鐩存挱闂村垏鐗囧伐浣滄祦瀛樺偍\{task_id}\` |
| 涓婁紶婧愯棰?| `source\鍘熸枃浠跺悕` |
| 鎻愬彇闊抽 | `audio\source.wav` |
| 杞啓 Markdown | `transcripts\transcript.md` |
| AI 鍒嗘瀽鏂囦欢 | `analysis\candidate_clips.json` |
| 杈撳嚭鍒囩墖 | `05_clips\` |
| 澶勭悊鏃ュ織 | `logs\process.log` |

