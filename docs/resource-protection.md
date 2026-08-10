# HSK 数据库资源保护与发布

## 安全边界

资源链路采用四层保护：SQLCipher 4 加密数据库页；KMS 信封加密每个资源版本的独立 DEK；客户端 X25519/Ed25519 设备密钥；以及覆盖下载 URL、密文摘要、版本和设备封装密钥的签名清单。

这能防止用户直接复制 URL、数据库文件或另一设备的清单后打开数据库，但不能绝对阻止具有本机管理员权限、调试器和进程内存读取能力的攻击者。桌面 DRM 的目标是显著提高提取成本，而不是宣称本地秘密永不可提取。

## 生产 KMS

当前实现支持 AWS KMS，也兼容提供 AWS KMS API 的私有端点：

- 一个对称 KMS key：API 运行角色只需 `kms:Decrypt`，发布角色需要 `kms:GenerateDataKey`。
- 一个 `ECC_NIST_P256 / SIGN_VERIFY` 非对称 KMS key：API 运行角色只需 `kms:Sign`，构建流程用 `kms:GetPublicKey` 导出公钥。
- 每次 KMS 数据密钥操作都绑定 `application/resource/version` Encryption Context。
- `ENV=prod` 时，代码会拒绝 `RESOURCE_KMS_PROVIDER=local` 和本地清单签名私钥。

API 主机使用 IAM Role / Workload Identity 获取 KMS 权限，不配置长期 AK/SK。完整变量模板见 `.env.example`。

## 首次部署

1. 迁移设备公钥字段：

   ```powershell
   uv run python -m scripts.migrate_resource_protection up
   ```


2. 将两个明文数据库转换为新的 SQLCipher 文件。输出文件必须与输入文件分离：

   ```powershell
   uv run python -m scripts.prepare_protected_resource `
     --resource-key hskCorpus `
     --version 2 `
     --input D:\private\hsk_corpus.db `
     --output D:\publish\hsk_corpus_v2.db

   uv run python -m scripts.prepare_protected_resource `
     --resource-key hskLocalCorpus `
     --version 2 `
     --input D:\private\hsk_corpus_local.db `
     --output D:\publish\hsk_corpus_local_v2.db
   ```

   脚本只输出密文 SHA-256、版本和 KMS wrapped key，不输出明文 DEK。
uv run python -m scripts.prepare_protected_resource --resource-key hskCorpus --version 2 --input "E:\Prismatica\PrismaticaUI\datas\corpora\hsk_corpus.db" --output "E:\hsk_corpus_v2.db"
HSK_CORPUS_SHA256=d1edfde392aff9f373b27bbfe4bb02a72da098000195b215cd4d0151909d4600
HSK_CORPUS_VERSION=2
HSK_CORPUS_KMS_WRAPPED_KEY=WEShCUHJQsyUlxsRWkFuH+Fbb5lculxuDPIyJbeAVMMqDQYpzFQal1wic1VbDU/+NLNXKNK5CNygwQcD

uv run python -m scripts.prepare_protected_resource --resource-key hskLocalCorpus --version 2 --input "E:\Prismatica\PrismaticaUI\datas\corpora\hsk_corpus_local.db" --output "E:\hsk_corpus_local_v2.db"
HSK_LOCAL_CORPUS_SHA256=2f220e0488c3cd3da5b4be344e3d0e15df65e3e8237c65db0b0181650686f66a
HSK_LOCAL_CORPUS_VERSION=2
HSK_LOCAL_CORPUS_KMS_WRAPPED_KEY=Es31fLS7tyOj3G5nWri8BmRxIoq3AqblzLVLcJb7CXJDxTmA7Cp0zpQLiWwCY4upHj/uA3yXOKA5BRgy

3. 只把 `D:\publish` 下的密文文件上传到私有源站，更新后端的 `SOURCE_URL / SHA256 / VERSION / KMS_WRAPPED_KEY`。旧的明文 URL 必须删除或关闭公网读取。

4. 导出清单签名公钥：

   ```powershell
   uv run python -m scripts.export_resource_signing_public_key
   ```

5. 把输出的 key ID 与 SPKI DER Base64 公钥写入 `PrismaticaUI/app/core/utils/resource_trust.py` 的 `TRUSTED_RESOURCE_MANIFEST_KEYS`，再构建生产客户端。公钥可以提交，私钥和 KMS 凭据不能提交。

6. 发布前在干净 Windows 虚拟机验证 `PRAGMA cipher_version`、启动下载、重启读取、退出登录后拒绝读取和设备撤销。

## 本地开发

```powershell
uv run python -m scripts.generate_resource_dev_keys --write-env .env
```

该命令会把 `RESOURCE_*` 私钥直接写入 API 的本地 `.env`，不会在终端显示私钥；公钥仍可安全复制到开发客户端。已有开发密钥时命令会拒绝覆盖，确认轮换后才使用 `--force`。不要提交这些值。生产环境不会接受 local 配置。

## 轮换与撤销

- 新资源版本重新生成 DEK、SQLCipher 文件和 KMS wrapped key，并提升版本号。
- KMS 主密钥使用原生自动轮换，或对 DEK 密文执行 ReEncrypt。
- 清单签名 key 轮换时，客户端先同时信任新旧公钥，再切换后端，最后移除旧公钥。
- 同一设备记录禁止静默覆盖公钥；密钥丢失时先撤销旧设备再重新登录。
