# CI/CD 自动化部署 · 考试手搓速记

> 场景：考试时给你一台空服务器（Ubuntu）+ 一个 GitHub 账号，要求从零搭一套「push 代码就自动上线」的 CI/CD。
> 目标：4 个阶段，照着敲就能跑通。文末有一页纸口诀。

---

## 总览：四阶段

```
① 建仓库拿 token  →  ② 服务器装 Runner  →  ③ 本地写配置文件  →  ④ push 验证上线
   GitHub 空仓库        Runner 显示 idle      Dockerfile + yml       网站能访问
```

核心认知（先记住这三句话，答辩用）：
- **yml 是"任务说明书"**：告诉 GitHub 触发后做什么。
- **Runner 是"干活的工人"**：装在服务器上，真正执行 docker 命令。
- **GitHub 是"派单调度中心"**：push 时把任务发给 Runner。
- 三者缺一不可。只有 yml 没有 Runner = 有说明书没工人；只有 Runner 没 yml = 有工人没活干。

---

## 阶段 ① GitHub 建仓库 + 拿 token

1. GitHub 新建一个空仓库，比如 `myapp`（**不要**勾选初始化 README，保持完全空）。
2. 拿 Runner 注册 token：
   - 仓库 → `Settings` → 左侧 `Actions` → `Runners` → `New self-hosted runner`
   - 选 `Linux` / `x64`，页面会给出下载和 config 命令，**同时给出 token**（形如 `AABBCC...`）。
   - ⚠️ token 有时效，拿到后尽快用；过期就回这个页面重新生成。

---

## 阶段 ② 服务器装 Runner（SSH 连上服务器后执行）

先确认服务器装了 Docker（`docker --version` 能出来就行）。然后：

```bash
# 1. 建目录并进入
mkdir actions-runner && cd actions-runner

# 2. 下载 Runner（版本号以 GitHub 页面显示的为准，这里用占位符）
curl -o actions-runner-linux-x64.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.XXX.0/actions-runner-linux-x64-2.XXX.0.tar.gz

# 3. 解压
tar xzf ./actions-runner-linux-x64.tar.gz

# 4. 注册到仓库（--token 换成阶段①拿到的）
./config.sh --url https://github.com/你的用户名/myapp --token AABBCC --labels self-hosted,aliyun

# 5. 装成系统服务并启动（这样服务器重启也能自动跑）
sudo ./svc.sh install
sudo ./svc.sh start
```

验证：回到 GitHub 仓库的 Runners 页面，看到 Runner 名字前面是**绿色 idle** 就成功了。

> **label 说明**：`--labels self-hosted,aliyun` 里的 `aliyun` 是自定义标签，
> 后面 yml 里要写 `runs-on: [self-hosted, aliyun]` 才能匹配上。两个地方必须一致，
> 否则任务会永远卡在排队。

---

## 阶段 ③ 本地写配置文件（4 个文件）

### 文件 1：`main.py`（业务代码，示意）

```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def index():
    return {"msg": "hello"}
```

### 文件 2：`requirements.txt`

```
fastapi
uvicorn
```

### 文件 3：`Dockerfile`（怎么把应用打包成镜像）

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 文件 4：`docker-compose.yml`（怎么运行容器）

```yaml
services:
  app:
    build: .
    container_name: myapp
    ports:
      - "8080:8000"        # 用 8080，别用 80（可能被占用）
    restart: always
```

### 文件 5：`.github/workflows/deploy.yml`（自动部署配置，核心！背这个）

```yaml
name: 自动部署                      # ① 叫什么

on:                                  # ② 什么时候触发
  push:
    branches: [main]
  workflow_dispatch:                  # 允许网页手动触发

jobs:
  deploy:
    runs-on: [self-hosted, aliyun]   # ③ 在哪跑：匹配自定义 label

    steps:                            # ④ 具体步骤：拉→建→验
      - name: 拉代码
        run: |
          cd /opt/myapp
          git reset --hard origin/main

      - name: 构建并启动容器
        run: cd /opt/myapp && docker compose up -d --build

      - name: 验证上线
        run: curl -f http://127.0.0.1:8080/
```

> **服务器要先建好目录并初始化 git**，否则第一步 `cd /opt/myapp` 会失败：
> ```bash
> mkdir -p /opt/myapp && cd /opt/myapp
> git init
> git remote add origin https://github.com/你的用户名/myapp.git
> git fetch origin main
> git reset --hard origin/main
> ```

---

## 阶段 ④ push 触发 + 验证

```bash
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/你的用户名/myapp.git
git push -u origin main
```

然后：
1. 打开 GitHub 仓库 → `Actions` 页，看到工作流自动开始跑。
2. 等它变绿（约 30 秒）。
3. 浏览器访问 `http://服务器IP:8080/`，看到 `{"msg":"hello"}` 就成功了。

⚠️ 如果服务器是云平台（阿里云/腾讯云），要去控制台**安全组放行 8080 端口**，否则外网访问不通（但 curl 本地 127.0.0.1 仍能通过）。

---

## 一页纸口诀（考前背这 6 行）

```
三要素：谁触发(on) · 在哪跑(runs-on) · 干什么(steps)
四阶段：建仓库拿token → 装Runner → 写配置 → push验证
yml四块：name / on / runs-on / steps
steps四步：拉代码 → 构建容器 → (同步前端) → 验证
label要一致：config 的 --labels  =  yml 的 runs-on
先建目录：/opt/项目名 要先 git init 否则 cd 失败
```

---

## 答辩标准答法（约 30 秒）

> "我的 CI/CD 用 GitHub Actions 加自托管 Runner 实现。分四步：
> 第一，在 GitHub 建仓库，拿到 Runner 的注册 token；
> 第二，在服务器上下载 Runner，用 config.sh 注册并配成系统服务开机自启，打上 `self-hosted` 和 `aliyun` 标签；
> 第三，在项目里写 Dockerfile 定义镜像、写 deploy.yml 定义触发条件和部署步骤——`runs-on` 要匹配前面的标签；
> 第四，push 到 main，GitHub 自动派任务给 Runner，它拉代码、重建容器、curl 验证，全程无人参与。
> 关键点有三个：label 两边必须一致、服务器目录要先初始化 git、云平台要放行端口。"

---

## 常见坑速查

| 现象 | 原因 | 解决 |
|---|---|---|
| 任务一直卡在排队(queued) | label 不匹配 | 检查 config 的 `--labels` 和 yml 的 `runs-on` 是否一致 |
| 工作流没触发 | yml 放错位置 / 分支不对 | 必须在 `.github/workflows/` 下，分支是 main |
| `cd: No such file` | 服务器目录没建 | 先手动 `mkdir /opt/myapp` 并 git init |
| 验证步骤失败 | 容器没起来 / 端口错 | 上服务器 `docker logs myapp` 看日志 |
| 外网访问不通 | 安全组没开端口 | 云平台控制台放行 8080 |
| token 无效 | 过期了 | 回 GitHub Runners 页面重新生成 |
| config 报 `Must not run with sudo` | 以 root 用户登录，runner 默认禁止 root 直接跑 | 命令前加开关：`RUNNER_ALLOW_RUNASROOT=1 ./config.sh ...`（注意是空格，不是 `&&`；跟 sudo、SUDO_USER 都无关） |
| config 卡在交互提问 / 把命令粘进提问里 | 交互提示（runner group 等）误粘贴命令 | 加 `--unattended --name 名字 --replace` 变非交互，全程无提问 |
| `sudo ./svc.sh` 报 command not found | 部分 runner 包没有根目录 svc.sh（服务脚本在 bin/runsvc.sh） | 不用 svc.sh，直接 `nohup ./run.sh > runner.log 2>&1 &` 后台运行 |
| YAML 报错 | 缩进错 / 用了 Tab | 只用空格，同级必须对齐 |
