# 医护节 - AI 证件审核后台（管理员端）

面向管理员的证件审核后台，上传图片后调用大模型分析识别医疗身份（职业类型、证件信息、真实性等）。

## 快速启动

```bash
cd medical-cert-mock-backend
python3 app.py
```

访问地址：**http://127.0.0.1:5001**

## 功能模块

| 页面 | 路径 | 说明 |
|------|------|------|
| 数据概览 | `/` | 审核统计、角色分布、最近记录 |
| 单张审核 | `/upload` | 上传一张证件图片，实时 AI 审核 |
| 批量审核 | `/batch` | 一次上传多张图片，批量审核 |
| 审核记录 | `/records` | 所有审核历史，支持按角色筛选 |
| 大模型配置 | `/settings` | 配置 API Key、选择模型、自定义 Prompt |

## 大模型配置

在 `/settings` 页面中支持三种模式：

### 1. 模拟模式（默认）
无需任何配置，使用本地规则模拟审核结果，适合功能演示和开发测试。

### 2. OpenAI GPT-4o
需要配置 OpenAI API Key，使用 GPT-4o 的视觉能力分析证件图片。

配置项：
- `openai_api_key`: API Key
- `openai_model`: 模型选择（gpt-4o / gpt-4o-mini / gpt-4-turbo）
- `openai_base_url`: 自定义 base URL（可选，用于代理或兼容接口）

### 3. Anthropic Claude
需要配置 Anthropic API Key，使用 Claude 的视觉能力。

配置项：
- `claude_api_key`: API Key
- `claude_model`: 模型选择（Claude Sonnet 4 / Opus 4 / 3.5 Sonnet）

### 自定义审核 Prompt
可以在设置页面自定义系统 Prompt，指导大模型如何分析证件。留空则使用默认 Prompt。

## 审核输出

大模型对每张证件图片输出以下信息：

- **证件类型**：医师资格证、护士执业证、学生证等
- **职业角色**：医生 / 护士 / 技师 / 药师 / 医学生 / 医院管理人员 / 其他
- **置信度**：0-1 的数值
- **提取信息**：姓名、证件编号、执业范围、职称、发证日期等
- **真实性评分**：0-1 的数值，评估证件视觉上的真实性
- **证件有效性**：有效 / 存疑
- **分析备注**：AI 判断依据说明

## 项目结构

```
medical-cert-mock-backend/
├── app.py                          # Flask 后端 + 大模型调用接口
├── config.json                     # 大模型配置（自动保存）
├── audit_data.json                 # 审核记录持久化存储
├── templates/admin/
│   ├── base.html                   # 布局模板（侧边栏 + 主内容区）
│   ├── dashboard.html              # 数据概览
│   ├── upload.html                 # 单张审核（拖拽上传 + 实时结果）
│   ├── batch.html                  # 批量审核（多图上传 + 进度条）
│   ├── records.html                # 审核记录列表（分页 + 筛选）
│   ├── detail.html                 # 审核详情（图片 + 结果对比）
│   └── settings.html               # 大模型配置（provider 选择 + 测试）
├── static/
│   ├── css/admin.css               # 全局样式
│   └── uploads/                    # 上传的图片存储
└── test_cert.png                   # 测试用证件图片
```

## 安装依赖

```bash
pip3 install flask Pillow
# 如需使用 OpenAI：
pip3 install openai
# 如需使用 Claude：
pip3 install anthropic
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload` | 上传图片并触发 AI 审核 |
| GET | `/api/records` | 获取审核记录（分页 + 筛选） |
| GET | `/api/records/<id>` | 获取单条记录详情 |
| DELETE | `/api/records/<id>` | 删除单条记录 |
| POST | `/api/settings` | 保存大模型配置 |
| GET | `/api/stats` | 获取统计数据 |

## 扩展：接入真实大模型

1. 启动服务后访问 `/settings` 页面
2. 选择 OpenAI 或 Claude 作为提供商
3. 填入对应的 API Key
4. 选择模型版本
5. 点击「保存配置」，页面会自动刷新
6. 使用「连接测试」功能验证是否正常工作

也可以直接编辑 `config.json` 文件：

```json
{
  "llm_provider": "openai",
  "openai_api_key": "sk-your-key-here",
  "openai_model": "gpt-4o"
}
```
