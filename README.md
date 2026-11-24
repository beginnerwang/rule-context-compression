# Rule Context Compression

一个按 token 预算压缩上下文的 Python 工具。给定问题，`read` 从文档选取带字符坐标的原文证据；给定工具结果，`tool` 将文本日志、JSON 或 JSONL 打包为指定预算内的视图。两者在本地运行，使用随包提供的 Qwen tokenizer 计数，无需模型服务。

## 安装

需要 Python 3.10 或更高版本。在仓库根目录运行：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
context-compress --help
```

在 Windows PowerShell 中，用 `py -m venv .venv` 创建环境，以 `.\.venv\Scripts\Activate.ps1` 激活。安装后也可以通过 `python -m context_compression` 调用相同的命令。

## 快速体验

以下示例适用于 POSIX shell，输入均在命令中提供。压缩结果写到标准输出，实际 token 用量和原文标识 `source_id` 写到标准错误。需要读取文件时，用 `--input 文件名` 代替标准输入。

**从文档选取原文。** 输出的每个片段含 `start`、`end` 和 `text`，满足 `text == source[start:end]`。

```sh
printf '%s\n' '北站每日八点开放。南站每日九点开放。' |
  context-compress read --question '南站几点开放？'
```

**压缩工具输出。** 下面生成 40 条作业记录，查询失败作业和错误码。最终视图最多使用 128 个 tokenizer token。

```sh
python -c 'import json; print(json.dumps([{"id": n, "status": "failed" if n == 23 else "ok", "error": "E_TIMEOUT" if n == 23 else None} for n in range(40)]))' |
  context-compress tool --format json --question 'Which job failed and what is its error code?' --budget 128
```

**回查原文。** 下面先保存一条工具结果，再从收据取出来源标识，按 JSON Pointer 读取 `E_TIMEOUT`。

```sh
mkdir -p runtime
printf '%s' '{"job":23,"error":"E_TIMEOUT"}' |
  context-compress tool --format json 2>runtime/receipt.json
source_id=$(python -c 'import json; print(json.load(open("runtime/receipt.json"))["source_id"])')
context-compress restore "$source_id" --pointer /error
```

## 命令与预算

| 命令 | 行为 |
| --- | --- |
| `read --question TEXT` | 使用 BM25 多路检索与倒数排名融合选择句段，再补入标题、定义、条件或有关联的原文对。选择题可以通过 `--options '{"A":"…","B":"…"}'` 提供全部选项。 |
| `tool --budget N` | 默认预算为 1024。可用 `--format auto\|log\|json\|jsonl` 指定格式。预算内原样通过。超预算时，日志保留相关行或生成可逆模板，JSON 尝试紧凑编码、列式表示和字段筛选，JSONL 支持按来源行筛选以及 ripgrep 搜索事件。 |
| `restore SOURCE_ID` | 返回保存的完整输入，也可选用 `--lines 2:5`、`--chars 10:20` 或 `--pointer /items/0`。行号从 1 起并包含两端，字符坐标从 0 起且终点不包含。 |
| `decode-template` | 从标准输入或 `--input` 读取完整的 `line_templates` 视图，校验摘要后逐字节还原日志。 |

阅读预算按最终序列化的证据 JSON 计算，上限为 **4096 token**。规则核心最多占 3072 token，补充槽可借用剩余额度。坐标和 JSON 包装也占预算，短文可能因此变长。工具输出可用 `--mode baseline` 对照基础日志与 JSON 规则。带标量首行的 JSONL 应显式使用 `--format jsonl`。

默认原文库存于当前工作目录下的 `runtime/originals/`。使用自定义位置时，在压缩与回查命令中传入相同的 `--store DIR`。

## 安装为 DSH 插件

可选的 `dsh-plugin/` 是独立的 JavaScript ESM/Cordis bundle，通过 DSH 的公开工具接口调用本包。已针对 DSH `0.2.1-rc.5` 验证。先依照上方步骤将 Python 包安装到虚拟环境，再从源码根目录向使用的 DSH profile 添加 bundle。以下命令要求 `dsh` 和 `pnpm` 在 PATH 中。如果使用 DSH 源码构建，可用 `node /path/to/deepseek-harness/apps/cli/lib/bin.js` 代替 `dsh`。

```sh
dsh plugin --profile headless add --offline --ignore-scripts "link:$PWD/dsh-plugin"
dsh plugin --profile web add --offline --ignore-scripts "link:$PWD/dsh-plugin"
```

链接安装会引用本地插件目录。只使用一个 profile 时执行对应的一条即可。向对应的 `$DSH_HOME/profiles/<profile>/cordis.patch.yml` 追加以下 YAML 行，保留已有配置。默认 `DSH_HOME` 为 `~/.dsh`。把示例中的两个绝对路径换成已安装本包的 Python 解释器和允许读取的 UTF-8 文件。可用 `python -c "import sys; print(sys.executable)"` 查询解释器路径。

```yaml
- id: rule-context-compression
  config:
    python: /absolute/path/to/.venv/bin/python
    reading:
      enabled: true
      documents:
        project-readme: /absolute/path/to/rule-context-compression/README.md
    output:
      enabled: true
      tools: [grep, glob]
      budget: 1024
```

重新启动已运行的 profile，新会话即可使用。可先用 `dsh headless --dump-config` 或 `dsh web --dump-config` 检查最终配置。DSH 的配置覆盖以完整 `config` 为单位，编辑时将两个开关与 Python 路径放在同一个插件配置块内。

- **按需阅读。** DSH 在需要阅读时调用 `compress_reading`，传入 `document_id`、`question`，选择题可另传字符串映射 `options`。可以在会话里问“请用 project-readme 的原文说明如何安装插件”。文件内容通过 stdin 送入压缩器，模型收到最多 4096 token 的原文证据 JSON。文档 ID 是管理员授予该 profile 的读取范围，因此只登记允许该 profile 内 Agent 阅读的文件。默认目录表为空。单个普通 UTF-8 文件上限 16 MiB，符号链接不受支持。若文件被原子替换，重新加载插件以绑定新文件。设置 `reading.enabled: false` 会注销阅读工具。PTC 模式由模型从 `run_code` 的工具 SDK 调用它。
- **按工具名压缩。** 设置 `output.enabled: true` 后，`tools` 中的名字按精确匹配处理，空列表可以停止处理所有工具。仅压缩匹配的顶层、成功且只有一个文本块的结果。Python 以最近的用户问题作为筛选依据，必要时使用工具的查询参数。只替换返回给模型的文本，供程序使用的原始 `value` 保留。对原样通过、错误、图文混合、PTC 内部子调用及其他插件已替换的结果，沿用 DSH 的返回值；压缩器出错时记录告警并返回原结果。`budget` 是成功压缩的文本预算。设置 `output.enabled: false` 可以单独关闭此处理。
- **原文回查。** 插件默认使用 `$DSH_HOME/context-compression/originals/` 保存原文，可在 `config.store` 中指定绝对路径。发生输出压缩时，DSH 日志包含实际输入与输出 token 以及 `source_id`。操作者可使用同一 Python 环境执行 `context-compress restore SOURCE_ID --store /absolute/path/to/originals` 取回原文。

### 离线验证与卸载

设置以下环境变量后，测试会创建临时 profile，用 DSH CLI 执行真实工具管线。测试覆盖四种开关组合、来源限制、白名单、预算、保留原始值和 Python 故障回退。整个测试使用本地样例。

```sh
DSH_CLI_ENTRY=/absolute/path/to/deepseek-harness/apps/cli/lib/bin.js \
RCC_TEST_PYTHON=/absolute/path/to/.venv/bin/python \
node tests/dsh-plugin.mjs
```

卸载时，从各 profile 的 `cordis.patch.yml` 中移除 `rule-context-compression` 配置行，再执行对应命令并重启该 profile。Python 命令行工具仍可单独使用。

```sh
dsh plugin --profile headless remove dsh-plugin-rule-context-compression
dsh plugin --profile web remove dsh-plugin-rule-context-compression
```

## 组员配对测试

**LongBench v2（200 题，400 次作答）。** 下载[清单](benchmarks/longbench-200.json)内 `data_url` 指向的官方数据，校验 `data_sha256`，按 `ids` 的顺序取题。清单已排除先导测试的 5 题，并预检完整输入窗口，含 105 道短题、95 道中等长度题。关闭工具输出压缩。阅读关闭组向 DSH 提交完整原文；开启组先按需调用一次 `compress_reading`，将最多 4096 token 的带坐标原文证据提交给相同的 DSH 作答器。两组使用相同证据 JSON 结构、问题、四个选项和系统提示；每题每组仅生成一个答案，奇数题先关后开、偶数题先开后关。沿用先导设置 `deepseek-flash`、`reasoningEffort=low`、温度 0.1、最大输出 16384 token。作答时关闭其他工具，输出 `{"final_answer":"A"}` 形式的一个选项（A–D）。从回答完成事件读取实际模型用量，缓存命中已计入完整输入。统计各组正确数/200、输入、输出、总 token，另列缓存、失败和重试。参考答案仅在评分时载入。

**工具输出（200 份日志，400 次工具调用）。** 在测试前冻结 200 份独立、已脱敏且已知答案的日志及其校验值。单点查找、跨行关联、分组极值、状态更正各选 50 份。每份配一个问题，并将标准答案拆成可核对的事实，记录对应实体或事件。让 DSH 原生 `grep` 针对每份日志的全部非空记录使用同一匹配式 `.`，确认原始返回包含完整记录且超过 1024 token。两个临时 profile 均关闭阅读压缩，输出白名单均为 `[grep]`，只切换 `output.enabled`，开启组的 `output.budget` 设为 1024。向插件提供问题，评分器单独读取答案。以随包 tokenizer 统计模型可见的工具文本，核对两组原始结构化结果一致。报告保留的事实数/全部事实数、所有答案事实均保留的日志数/200、两组可见 token 合计和降幅。工具实验全程在本地执行。

**先导结果。** 另一次 5 题阅读测试中，关闭和开启压缩分别答对 4 题和 3 题，10 次可评分作答的实际输入合计分别为 153,018 和 20,361 token；一次未取得用量回执的额外全文请求单列。10 份合成日志的可见输出从 51,054 降至 9,950 token，保留事实从 25/25 变为 22/25。正式测试请保存样本清单、逐项结果和失败明细，另行报告。

## 项目结构

| 位置 | 用途 |
| --- | --- |
| `dsh-plugin/`、`tests/dsh-plugin.mjs` | DSH 插件、配置与离线集成测试 |
| `context_compression/reading/` | 原文切分、词法检索、结构关联和证据打包 |
| `context_compression/tool_output/` | 日志、JSON、JSONL 压缩与可逆模板 |
| `context_compression/assets/tokenizer.json` | 固定版本的 tokenizer 数据 |
| `context_compression/cli.py`、`sources.py`、`tokens.py`、`json_codec.py` | 命令行、原文存取、token 计数和 JSON 编解码 |

## 来源与第三方声明

工具模块采用独立 Python 实现，借鉴 Headroom 的结构化压缩思路，并采用 TokenSnip 的行模板和结构化搜索机制。下列声明适用于相应第三方材料。[Headroom](https://github.com/headroomlabs-ai/headroom) · [TokenSnip 源码版本](https://github.com/Happy1buffalo/tokensnip/tree/49117fe51d66fd82d1d0b84511f76aa26cb98037)

分词数据取自 Qwen3.5-27B-GPTQ-Int4 的固定版本，文件 SHA-256 为 `5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42`。[Qwen tokenizer 来源](https://huggingface.co/Qwen/Qwen3.5-27B-GPTQ-Int4/tree/8f0c09f227ae570e79617c6d9172b59df9c16081)

<details>
<summary>TokenSnip / DSH Sieve · MIT</summary>

```text
MIT License

Copyright (c) 2026 ZhengYT888

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The upstream dsh-sieve package includes the following additional third-party notice.

MIT License

Copyright (c) 2025 Mario Zechner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

</details>

<details>
<summary>Qwen tokenizer · Apache License 2.0</summary>

```text
Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS
```

</details>
