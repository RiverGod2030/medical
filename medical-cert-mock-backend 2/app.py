#!/usr/bin/env python3
"""
医护节 - AI 证件审核后台（管理员端）

功能：
1. 上传证件图片，调用大模型分析识别医疗身份
2. 人工复审：置信度 50%-90% 的记录标记为待复审
3. 重复检测：证件编号或姓名重复自动标记
4. 查看审核历史和统计
5. 支持批量上传

大模型接口配置：在 config.json 中配置 API Key 和模型
"""

import os
import json
import uuid
import time
import base64
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, render_template, jsonify

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
CONFIG_FILE = BASE_DIR / "config.json"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 配置加载
# ============================================================

def load_config():
    default_config = {
        "llm_provider": "zhipu",
        "openai_api_key": "",
        "openai_model": "gpt-4o",
        "openai_base_url": "",
        "claude_api_key": "",
        "claude_model": "claude-sonnet-4-20250514",
        "zhipu_api_key": "",
        "zhipu_model": "glm-4v-flash",
        "audit_prompt": ""
    }
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            default_config.update(saved)
    return default_config

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

config = load_config()

# ============================================================
# 数据存储
# ============================================================

DATA_FILE = BASE_DIR / "audit_data.json"

def load_audit_records():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_audit_records(records):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

audit_records = load_audit_records()

# ============================================================
# 重复检测
# ============================================================

def detect_duplicate(record):
    """
    检测是否有重复的证件信息
    基于证件编号和姓名进行匹配
    返回 (is_duplicate, matched_record_id, duplicate_fields)
    """
    if record.get("status") != "completed":
        return False, None, []

    info = record.get("result", {}).get("extracted_info", {})
    cert_no = info.get("证件编号", "")
    name = info.get("姓名", "")

    if not cert_no and not name:
        return False, None, []

    dup_fields = []
    for existing in audit_records:
        if existing.get("status") != "completed":
            continue
        if existing["id"] == record["id"]:
            continue

        existing_info = existing.get("result", {}).get("extracted_info", {})
        existing_cert_no = existing_info.get("证件编号", "")
        existing_name = existing_info.get("姓名", "")

        if cert_no and existing_cert_no and cert_no == existing_cert_no:
            dup_fields.append("证件编号")
        if name and existing_name and name == existing_name:
            dup_fields.append("姓名")

        if dup_fields:
            return True, existing["id"], dup_fields

    return False, None, []

# ============================================================
# 审核状态判定
# ============================================================

def determine_review_status(confidence, authenticity_score=1.0):
    """
    根据置信度和真实性判定审核状态
    confidence >= 0.9 且 authenticity >= 0.9 -> auto_passed (自动通过)
    confidence < 0.9 或 authenticity < 0.9 -> needs_review (待人工复审)
    confidence < 0.5 -> auto_rejected (自动拒绝)
    """
    if confidence < 0.5:
        return "auto_rejected"
    if confidence >= 0.9 and authenticity_score >= 0.9:
        return "auto_passed"
    return "needs_review"

# ============================================================
# 大模型调用
# ============================================================

AUDIT_SYSTEM_PROMPT = """你是一个专业的医疗证件审核 AI。请分析上传的证件图片，完成以下任务：

1. **证件类型识别**：判断这是什么类型的证件（医师资格证、医师执业证、护士资格证、护士执业证、医院工作证、学生证、职称证书、规培证等）
2. **职业角色判断**：根据证件内容判断持证人的职业角色，从以下类别中选择：医生、护士、技师（医技人员）、药师、医学生、医院管理人员、其他
3. **关键信息提取**：从证件中提取以下信息（如可见）：姓名、证件编号、执业范围/专业、职称/级别、发证日期、有效期、发证机关、工作单位/学校
4. **证件真实性评估**：评估证件看起来是否真实有效（基于排版、印章、格式等视觉特征）

请以 JSON 格式输出：
{"certificate_type":"证件类型","detected_role":"医生|护士|技师|药师|医学生|医院管理人员|其他","confidence":0.0-1.0,"extracted_info":{"姓名":"xxx","证件编号":"xxx",...},"authenticity_score":0.0-1.0,"is_likely_valid":true/false,"analysis_notes":"分析备注"}

只输出 JSON，不要其他内容。"""


def call_llm_with_image(image_base64, mime_type):
    provider = config.get("llm_provider", "mock")
    dispatch = {
        "openai": _call_openai,
        "claude": _call_claude,
        "zhipu": _call_zhipu,
    }
    fn = dispatch.get(provider, _call_mock)
    return fn(image_base64, mime_type)


def _call_openai(image_base64, mime_type):
    api_key = config.get("openai_api_key", "")
    if not api_key:
        return {"error": "未配置 OpenAI API Key", "provider": "openai"}
    try:
        from openai import OpenAI
        kw = {"api_key": api_key}
        bu = config.get("openai_base_url", "")
        if bu:
            kw["base_url"] = bu
        client = OpenAI(**kw)
        prompt = config.get("audit_prompt") or AUDIT_SYSTEM_PROMPT
        resp = client.chat.completions.create(
            model=config.get("openai_model", "gpt-4o"),
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": "请分析这张医疗证件图片，识别证件类型、持证人角色，并提取关键信息。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}", "detail": "high"}}
                ]}
            ],
            max_tokens=2000, temperature=0.1
        )
        return _parse_json(resp.choices[0].message.content.strip(), "openai")
    except ImportError:
        return {"error": "未安装 openai 库，请运行: pip install openai", "provider": "openai"}
    except Exception as e:
        return {"error": f"OpenAI API 调用失败: {str(e)}", "provider": "openai"}


def _call_claude(image_base64, mime_type):
    api_key = config.get("claude_api_key", "")
    if not api_key:
        return {"error": "未配置 Claude API Key", "provider": "claude"}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = config.get("audit_prompt") or AUDIT_SYSTEM_PROMPT
        msg = client.messages.create(
            model=config.get("claude_model", "claude-sonnet-4-20250514"),
            max_tokens=2000, system=prompt,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_base64}},
                {"type": "text", "text": "请分析这张医疗证件图片，识别证件类型、持证人角色，并提取关键信息。"}
            ]}]
        )
        return _parse_json(msg.content[0].text.strip(), "claude")
    except ImportError:
        return {"error": "未安装 anthropic 库，请运行: pip install anthropic", "provider": "claude"}
    except Exception as e:
        return {"error": f"Claude API 调用失败: {str(e)}", "provider": "claude"}


def _call_zhipu(image_base64, mime_type):
    api_key = config.get("zhipu_api_key", "")
    if not api_key:
        return {"error": "未配置智谱 API Key", "provider": "zhipu"}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4/")
        prompt = config.get("audit_prompt") or AUDIT_SYSTEM_PROMPT
        resp = client.chat.completions.create(
            model=config.get("zhipu_model", "glm-4v-flash"),
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": "请分析这张医疗证件图片，识别证件类型、持证人角色，并提取关键信息。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}}
                ]}
            ],
            temperature=0.1
        )
        return _parse_json(resp.choices[0].message.content.strip(), "zhipu")
    except ImportError:
        return {"error": "未安装 openai 库，请运行: pip install openai", "provider": "zhipu"}
    except Exception as e:
        return {"error": f"智谱 API 调用失败: {str(e)}", "provider": "zhipu"}


def _call_mock(image_base64, mime_type):
    import random
    random.seed(len(image_base64) + int(time.time() * 1000) % 10000)
    roles = ["医生", "护士", "技师", "药师", "医学生"]
    role = random.choice(roles)
    cert_types = {
        "医生": ["医师资格证书", "医师执业证书", "医院工作证", "规培证", "职称证书"],
        "护士": ["护士资格证书", "护士执业证书", "医院工作证", "护理职称证书"],
        "技师": ["技师资格证书", "检验技师证", "影像技师证"],
        "药师": ["药师资格证书", "执业药师证", "临床药师培训证书"],
        "医学生": ["学生证", "录取通知书", "实习证明", "研究生证"]
    }
    cert_type = random.choice(cert_types[role])
    surnames = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴"]
    given_names = ["伟", "芳", "敏", "静", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰"]
    name = random.choice(surnames) + random.choice(given_names)
    info = {"姓名": name, "证件编号": "".join([str(random.randint(0, 9)) for _ in range(18)])}
    if role == "医生":
        info["执业范围"] = random.choice(["内科", "外科", "妇产科", "儿科", "骨科"])
        info["职称"] = random.choice(["住院医师", "主治医师", "副主任医师", "主任医师"])
    elif role == "护士":
        info["科室"] = random.choice(["内科", "外科", "急诊科", "ICU", "手术室"])
        info["职称"] = random.choice(["护士", "护师", "主管护师", "副主任护师"])
    elif role == "技师":
        info["科室"] = random.choice(["检验科", "影像科", "病理科", "康复科"])
        info["职称"] = random.choice(["技士", "技师", "主管技师"])
    elif role == "药师":
        info["工作单位"] = random.choice(["医院药房", "社会药房"])
        info["职称"] = random.choice(["药士", "药师", "主管药师"])
    elif role == "医学生":
        info["学校"] = random.choice(["某某医科大学", "某某医学院"])
        info["专业"] = random.choice(["临床医学", "护理学", "药学", "口腔医学"])
    info["发证日期"] = f"20{random.randint(15,24):02d}年{random.randint(1,12):02d}月"
    info["有效期至"] = f"20{random.randint(25,30):02d}年{random.randint(1,12):02d}月"
    # 生成不同置信度的结果来测试人工复审
    confidence = round(random.uniform(0.45, 0.98), 2)
    authenticity = round(random.uniform(0.70, 0.95), 2)
    return {
        "certificate_type": cert_type, "detected_role": role, "confidence": confidence,
        "extracted_info": info, "authenticity_score": authenticity,
        "is_likely_valid": authenticity > 0.75,
        "analysis_notes": f"模拟结果：识别为{role}的{cert_type}，置信度{confidence}。" +
                          ("需要人工复审" if 0.5 <= confidence < 0.9 else "自动通过" if confidence >= 0.9 else "自动拒绝"),
        "provider": "mock"
    }


def _parse_json(text, provider):
    try:
        r = json.loads(text); r["provider"] = provider; return r
    except json.JSONDecodeError:
        pass
    import re
    m = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
    if m:
        try:
            r = json.loads(m.group(1)); r["provider"] = provider; return r
        except json.JSONDecodeError:
            pass
    s, e = text.find('{'), text.rfind('}') + 1
    if s >= 0 and e > s:
        try:
            r = json.loads(text[s:e]); r["provider"] = provider; return r
        except json.JSONDecodeError:
            pass
    return {"error": "大模型返回格式无法解析", "raw_response": text[:500], "provider": provider}


# ============================================================
# Flask App
# ============================================================

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024


@app.route("/")
def dashboard():
    return render_template("admin/dashboard.html", stats=compute_stats())

@app.route("/upload")
def upload_page():
    return render_template("admin/upload.html")

@app.route("/batch")
def batch_page():
    return render_template("admin/batch.html")

@app.route("/records")
def records_page():
    return render_template("admin/records.html")

@app.route("/review")
def review_page():
    """人工复审页面"""
    return render_template("admin/review.html")

@app.route("/duplicates")
def duplicates_page():
    """重复检测页面"""
    return render_template("admin/duplicates.html")

@app.route("/detail/<record_id>")
def detail_page(record_id):
    return render_template("admin/detail.html", record_id=record_id)

@app.route("/settings")
def settings_page():
    return render_template("admin/settings.html", config=config)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "image" not in request.files:
        return jsonify({"error": "请上传图片文件"}), 400
    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "请选择图片文件"}), 400

    ext = Path(file.filename).suffix or ".jpg"
    unique_name = f"{uuid.uuid4().hex[:12]}{ext}"
    save_path = UPLOAD_DIR / unique_name
    file.save(str(save_path))

    with open(save_path, "rb") as f:
        image_data = f.read()
    image_base64 = base64.b64encode(image_data).decode("utf-8")

    mime_map = {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".bmp":"image/bmp",".webp":"image/webp",".gif":"image/gif"}
    mime_type = mime_map.get(ext.lower(), "image/jpeg")

    start_time = time.time()
    result = call_llm_with_image(image_base64, mime_type)
    duration_ms = int((time.time() - start_time) * 1000)

    record = {
        "id": str(uuid.uuid4())[:10],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filename": file.filename,
        "image_path": f"/static/uploads/{unique_name}",
        "duration_ms": duration_ms,
        "submit_time": datetime.now().isoformat(),
        "result": result,
    }

    if "error" in result:
        record["status"] = "error"
    else:
        confidence = result.get("confidence", 0)
        authenticity = result.get("authenticity_score", 1.0)
        # 判定审核状态
        review_status = determine_review_status(confidence, authenticity)
        record["review_status"] = review_status
        record["status"] = "completed"
        record["detected_role"] = result.get("detected_role", "未知")
        record["certificate_type"] = result.get("certificate_type", "未知")
        record["confidence"] = confidence

        # 重复检测
        is_dup, dup_record_id, dup_fields = detect_duplicate(record)
        if is_dup:
            record["is_duplicate"] = True
            record["duplicate_of"] = dup_record_id
            record["duplicate_fields"] = dup_fields
            # 重复的直接标记为不通过
            record["review_status"] = "duplicate_rejected"
        else:
            record["is_duplicate"] = False

    audit_records.insert(0, record)
    save_audit_records(audit_records)

    return jsonify({
        "success": "error" not in result,
        "record_id": record["id"],
        "result": result,
        "review_status": record.get("review_status", "error"),
        "is_duplicate": record.get("is_duplicate", False),
        "duplicate_of": record.get("duplicate_of"),
        "duplicate_fields": record.get("duplicate_fields", []),
        "duration_ms": duration_ms
    })


@app.route("/api/review/<record_id>", methods=["POST"])
def api_manual_review(record_id):
    """人工复审：通过或拒绝"""
    data = request.json
    action = data.get("action")  # "approve" | "reject"
    comment = data.get("comment", "")

    for record in audit_records:
        if record["id"] == record_id:
            if action == "approve":
                record["review_status"] = "manually_approved"
                record["manual_review"] = {"action": "approve", "comment": comment, "reviewer": "admin", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            elif action == "reject":
                record["review_status"] = "manually_rejected"
                record["manual_review"] = {"action": "reject", "comment": comment, "reviewer": "admin", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            save_audit_records(audit_records)
            return jsonify({"success": True, "record": record})

    return jsonify({"error": "记录不存在"}), 404


@app.route("/api/appeal/<record_id>", methods=["POST"])
def api_appeal(record_id):
    """用户申诉：将记录直接送入人工复审流程"""
    data = request.json or {}
    reason = data.get("reason", "")

    for record in audit_records:
        if record["id"] == record_id:
            # 只有非人工处理过的记录才能申诉
            if record.get("review_status") in ("manually_approved", "manually_rejected"):
                return jsonify({"error": "该记录已经人工处理过，无法申诉"}), 400
            # 申诉后进入待复审队列
            record["review_status"] = "needs_review"
            record["appeal"] = {
                "reason": reason,
                "time": datetime.now().isoformat(),
                "status": "pending"
            }
            save_audit_records(audit_records)
            return jsonify({"success": True, "record": record})

    return jsonify({"error": "记录不存在"}), 404


@app.route("/api/records/<record_id>", methods=["PATCH"])
def api_update_record(record_id):
    """后台人工修改记录的核心信息"""
    data = request.json or {}
    allowed_fields = [
        "detected_role", "certificate_type", "confidence",
        "is_likely_valid", "authenticity_score"
    ]

    for record in audit_records:
        if record["id"] == record_id:
            # 更新顶层字段
            for f in allowed_fields:
                if f in data:
                    record[f] = data[f]

            # 更新提取信息
            if "extracted_info" in data:
                if "result" not in record:
                    record["result"] = {}
                if "extracted_info" not in record["result"]:
                    record["result"]["extracted_info"] = {}
                for k, v in data["extracted_info"].items():
                    record["result"]["extracted_info"][k] = v

            # 更新分析备注
            if "analysis_notes" in data:
                if "result" not in record:
                    record["result"] = {}
                record["result"]["analysis_notes"] = data["analysis_notes"]

            # 更新审核状态
            if "review_status" in data:
                record["review_status"] = data["review_status"]

            # 记录修改痕迹
            record.setdefault("manual_edits", []).append({
                "fields": list(data.keys()),
                "editor": "admin",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

            save_audit_records(audit_records)
            return jsonify({"success": True, "record": record})

    return jsonify({"error": "记录不存在"}), 404


@app.route("/api/records")
def api_records():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    role_filter = request.args.get("role", "")
    status_filter = request.args.get("status", "")  # needs_review, auto_passed, etc.

    filtered = audit_records
    if role_filter:
        filtered = [r for r in filtered if r.get("detected_role") == role_filter]
    if status_filter:
        filtered = [r for r in filtered if r.get("review_status") == status_filter]

    total = len(filtered)
    start = (page - 1) * per_page
    end = start + per_page
    return jsonify({"records": filtered[start:end], "total": total, "page": page, "per_page": per_page})


@app.route("/api/duplicates")
def api_duplicates():
    """获取所有重复记录"""
    dups = [r for r in audit_records if r.get("is_duplicate")]
    return jsonify({"duplicates": dups, "total": len(dups)})


@app.route("/api/records/<record_id>")
def api_record_detail(record_id):
    for record in audit_records:
        if record["id"] == record_id:
            return jsonify({"record": record})
    return jsonify({"error": "记录不存在"}), 404


@app.route("/api/records/<record_id>", methods=["DELETE"])
def api_delete_record(record_id):
    global audit_records
    before = len(audit_records)
    audit_records = [r for r in audit_records if r["id"] != record_id]
    if len(audit_records) < before:
        save_audit_records(audit_records)
        return jsonify({"success": True})
    return jsonify({"error": "记录不存在"}), 404


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json
    config.update(data)
    save_config(config)
    return jsonify({"success": True, "config": config})


@app.route("/api/stats")
def api_stats():
    return jsonify(compute_stats())


def compute_stats():
    total = len(audit_records)
    completed = sum(1 for r in audit_records if r.get("status") == "completed")
    errors = sum(1 for r in audit_records if r.get("status") == "error")

    # Review status breakdown
    review_breakdown = {}
    for r in audit_records:
        rs = r.get("review_status", "unknown")
        review_breakdown[rs] = review_breakdown.get(rs, 0) + 1

    needs_review = review_breakdown.get("needs_review", 0)
    auto_passed = review_breakdown.get("auto_passed", 0)
    auto_rejected = review_breakdown.get("auto_rejected", 0)
    duplicate_count = sum(1 for r in audit_records if r.get("is_duplicate"))
    appeal_count = sum(1 for r in audit_records if r.get("appeal") and r.get("appeal", {}).get("status") == "pending")

    # SLA: pending records with timeout
    now = datetime.now()
    sla_timeout = 0
    for r in audit_records:
        if r.get("review_status") == "needs_review" and "submit_time" in r:
            try:
                st = datetime.fromisoformat(r["submit_time"])
                if (now - st).total_seconds() > 86400:
                    sla_timeout += 1
            except (ValueError, TypeError):
                pass

    role_counts = {}
    cert_counts = {}
    for r in audit_records:
        if r.get("status") == "completed":
            role = r.get("detected_role", "未知")
            role_counts[role] = role_counts.get(role, 0) + 1
            cert = r.get("certificate_type", "未知")
            cert_counts[cert] = cert_counts.get(cert, 0) + 1

    confidences = [r.get("confidence", 0) for r in audit_records if r.get("status") == "completed"]
    avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0
    durations = [r.get("duration_ms", 0) for r in audit_records if r.get("duration_ms")]
    avg_duration = round(sum(durations) / len(durations), 0) if durations else 0

    return {
        "total": total, "completed": completed, "errors": errors,
        "review_breakdown": review_breakdown,
        "needs_review": needs_review, "auto_passed": auto_passed,
        "auto_rejected": auto_rejected, "duplicate_count": duplicate_count,
        "appeal_count": appeal_count, "sla_timeout": sla_timeout,
        "role_counts": role_counts, "cert_counts": cert_counts,
        "avg_confidence": avg_confidence, "avg_duration_ms": avg_duration,
        "llm_provider": config.get("llm_provider", "mock")
    }


if __name__ == "__main__":
    print("=" * 60)
    print("  医护节 - AI 证件审核后台（管理员端）")
    print(f"  大模型提供商: {config.get('llm_provider', 'mock')}")
    print(f"  访问地址: http://127.0.0.1:5001")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5001, debug=True)
