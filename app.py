import re
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# --- 时间码处理辅助函数 (与之前版本相同) ---
def timecode_to_milliseconds_normalized(tc_str_single):
    match = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d+)", tc_str_single.strip())
    if not match:
        return None
    h, m, s, ms_val_str = match.groups()
    h, m, s = int(h), int(m), int(s)
    if len(ms_val_str) > 3: ms = int(ms_val_str[:3])
    elif len(ms_val_str) < 3: ms = int(ms_val_str.ljust(3, '0'))
    else: ms = int(ms_val_str)
    return (h * 3600 + m * 60 + s) * 1000 + ms

def milliseconds_to_timecode(ms_total):
    if ms_total is None or ms_total < 0: return "时间无效"
    millis = int(ms_total % 1000)
    seconds_total = int(ms_total // 1000)
    s = seconds_total % 60
    minutes_total = seconds_total // 60
    m = minutes_total % 60
    h = minutes_total // 60
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"

# --- 字幕解析与合并函数 (与之前版本相同) ---
def parse_srt_content(content):
    entries = {}
    content = content.replace('\r\n', '\n')
    pattern = re.compile(
        r"(\d+)\s*\n"
        r"(\d{2}:\d{2}:\d{2}[,.]\d+\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d+)\s*\n"
        r"([\s\S]*?)"
        r"(?=\n\n\d+\s*\n|\Z)",
        re.MULTILINE
    )
    for match in pattern.finditer(content):
        seq, timecode, text = match.group(1).strip(), match.group(2).strip().replace('.', ','), match.group(3).strip()
        if text: entries[seq] = {'timecode': timecode, 'text': text}
    return entries

def process_chinese_text_for_parentheses(text_block):
    lines = text_block.split('\n')
    # 先移除括号，再移除处理后可能产生的多余空格，最后过滤掉完全变为空的行
    processed_lines = []
    for line_content in lines:
        temp_line = re.sub(r'（[^）]*）', '', re.sub(r'\([^)]*\)', '', line_content)).strip()
        if temp_line: # 只添加非空行
             processed_lines.append(" ".join(temp_line.split()))
    return "\n".join(processed_lines)


def merge_subtitles(eng_subs, chs_subs):
    merged_subs = []
    # 以英文字幕的顺序为基准进行合并
    sorted_eng_keys = sorted(eng_subs.keys(), key=int)

    for seq in sorted_eng_keys:
        entry = {"seq": seq, "timecode": eng_subs[seq]['timecode']}
        english_text = eng_subs[seq]['text']
        # 从处理过的中文字幕中获取文本
        chinese_text_entry = chs_subs.get(seq, {})
        chinese_text = chinese_text_entry.get('text', '') if isinstance(chinese_text_entry, dict) else ''


        if chinese_text.strip(): # 确保中文文本处理后不为空白
            entry["text"] = f"{chinese_text}\n{english_text}" # 中文在上，英文在下
        else: # 如果没有中文翻译或中文翻译处理后为空，则只保留英文
            entry["text"] = english_text
        merged_subs.append(entry)
    return merged_subs

def format_output_srt(merged_data):
    output_lines = []
    for entry in merged_data:
        output_lines.append(entry['seq'])
        output_lines.append(entry['timecode'])
        output_lines.append(entry['text'])
        output_lines.append("")  # 空行分隔条目
    return "\n".join(output_lines).strip()

CHINESE_PUNCTUATIONS_TO_FLAG = {'；', '，', '。', '！', '“', '”', '（', '）'}

# --- 主页面路由 ---
@app.route('/', methods=['GET', 'POST'])
def index():
    # 初始化所有可能在模板中使用或在POST中修改的变量
    merged_output = ""
    merge_error_message = ""
    eng_input_text = ""
    chs_input_text = ""
    check_content_input = "" # 用于回填检查文本框

    if request.method == 'POST':
        # 合并字幕功能保持表单提交方式
        if 'submit_merge' in request.form:
            eng_input_text = request.form.get('eng_content', '')
            chs_input_text = request.form.get('chs_content', '')

            if not eng_input_text.strip() or not chs_input_text.strip():
                merge_error_message = "英文字幕内容和中文翻译内容均不能为空。"
            else:
                try:
                    eng_subs = parse_srt_content(eng_input_text)
                    chs_subs = parse_srt_content(chs_input_text)

                    current_errors = [] # 用于收集本次合并操作的错误/警告

                    if not eng_subs:
                        current_errors.append("无法解析英文字幕内容，请检查格式。")
                    if not chs_subs:
                        current_errors.append("无法解析中文字幕内容，请检查格式。")

                    if not current_errors: # 只有在两份字幕都至少解析出某些内容（即使是空的字典）后才继续
                        # 1. 处理中文字幕中的括号
                        for seq_num in chs_subs:
                            if 'text' in chs_subs[seq_num]:
                                chs_subs[seq_num]['text'] = process_chinese_text_for_parentheses(chs_subs[seq_num]['text'])

                        # 2. 时间轴一致性检查
                        timestamp_errors_list = []
                        proceed_to_merge_after_ts_check = True
                        # 检查共有的序号的时间轴
                        common_keys = set(eng_subs.keys()) & set(chs_subs.keys())
                        for seq_num in common_keys:
                            ch_entry = chs_subs[seq_num]
                            eng_entry = eng_subs[seq_num]
                            # 规范化时间码字符串以进行比较（去除多余空格, 统一毫秒分隔符为逗号）
                            norm_ch_timecode = ' '.join(ch_entry['timecode'].split()).replace('.',',')
                            norm_eng_timecode = ' '.join(eng_entry['timecode'].split()).replace('.',',')
                            if norm_ch_timecode != norm_eng_timecode:
                                timestamp_errors_list.append(
                                    f"错误：序号 {seq_num} - 中文时间轴 \"{ch_entry['timecode']}\" 与英文时间轴 \"{eng_entry['timecode']}\" 不一致。"
                                )
                                proceed_to_merge_after_ts_check = False

                        if not proceed_to_merge_after_ts_check:
                            current_errors.append("时间轴检查失败，合并终止。")
                            current_errors.extend(timestamp_errors_list)
                        else:
                            # 3. 执行合并
                            merged_data = merge_subtitles(eng_subs, chs_subs)

                            # 4. 检查中文文本是否被有效使用
                            chinese_text_was_actually_used = False
                            if chs_subs: # 仅当提供了中文字幕（且解析后chs_subs非空）
                                for eng_seq_key in eng_subs: # 迭代英文字幕的键
                                    # 检查此英文序号是否存在于中文，且处理后的中文内容不为空
                                    if eng_seq_key in chs_subs and chs_subs[eng_seq_key].get('text', '').strip():
                                        chinese_text_was_actually_used = True
                                        break

                            if chs_subs and not chinese_text_was_actually_used and eng_subs: # 如果有中文字幕但没用上，且有英文字幕作为参照
                                current_errors.append("警告：提供了中文字幕，但未能与任何英文字幕条目成功匹配并合并有效的中文内容（可能因序号不匹配或处理后内容为空）。")

                            merged_output = format_output_srt(merged_data)
                            if not merged_output and not current_errors: # 如果输出为空且之前没有其他错误
                                current_errors.append("合并结果为空（可能所有输入处理后均无有效内容，或没有匹配的条目）。")

                    if current_errors:
                        merge_error_message = "\n".join(current_errors)

                except Exception as e:
                    merge_error_message = f"合并处理时发生意外错误: {str(e)}"

        # 保留用户在“检查内容”文本框中的输入，以防他们错误地通过合并表单提交
        # 主要的检查逻辑在AJAX路由中
        check_content_input = request.form.get('check_content', '')


    return render_template('index.html',
                           merged_output=merged_output, merge_error_message=merge_error_message,
                           eng_input=eng_input_text, chs_input=chs_input_text,
                           check_content_input=check_content_input, # 用于回填
                           # 以下用于初始加载或非AJAX提交时的默认值，AJAX会动态更新这些
                           check_errors=[],
                           check_success_message="",
                           show_confetti=False,
                           confetti_trigger=None)

# --- AJAX 格式检查路由 (/ajax_check_format) ---
# (与您当前文档中的版本相同，此处省略以保持简洁)
@app.route('/ajax_check_format', methods=['POST'])
def ajax_check_format():
    data = request.get_json()
    check_input_text = data.get('check_content', '')
    check_errors = []
    check_success_message = ""
    show_confetti = False

    if not check_input_text.strip():
        check_errors.append("用于格式检查的输入内容为空。")
    else:
        normalized_content = check_input_text.replace('\r\n', '\n').strip()
        if not normalized_content.endswith("\n\n"): normalized_content += "\n\n"
        blocks = [block.strip() for block in normalized_content.split('\n\n') if block.strip()]

        parsed_blocks_for_sequential_check = []
        timecode_overall_pattern = r"\d{2}:\d{2}:\d{2}[,.]\d+\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d+"
        single_timecode_strict_ms_pattern = r"\d{2}:\d{2}:\d{2}[,.](\d{3})"

        if not blocks:
            check_errors.append("未检测到有效的字幕段落用于检查。")
        else:
            # --- 单块检查逻辑 (与之前版本中的格式检查部分相同) ---
            for i, block_text in enumerate(blocks):
                lines = block_text.split('\n')
                is_this_block_valid = True
                block_error_details = []
                current_seq_val, current_start_ms, current_end_ms = None, None, None

                if len(lines) < 4:
                    is_this_block_valid = False
                    block_error_details.append(f"结构错误：期望至少4行，实际 {len(lines)} 行。")
                else:
                    if not lines[0].strip().isdigit():
                        is_this_block_valid = False
                        block_error_details.append(f"序号错误：行1 \"{lines[0]}\" 不是数字。")
                    else: current_seq_val = int(lines[0].strip())

                    time_str_original = lines[1].strip()
                    if not re.fullmatch(timecode_overall_pattern, time_str_original):
                        is_this_block_valid = False
                        block_error_details.append(f"时间轴整体格式错误：行2 \"{time_str_original}\"。")
                    else:
                        tc_parts = [p.strip() for p in time_str_original.split("-->")]
                        tc_start_str, tc_end_str = tc_parts[0], tc_parts[1]
                        if not re.fullmatch(single_timecode_strict_ms_pattern, tc_start_str.replace('.',',')):
                            is_this_block_valid = False
                            block_error_details.append(f"开始时间毫秒位数错误: \"{tc_start_str}\"。")
                        if not re.fullmatch(single_timecode_strict_ms_pattern, tc_end_str.replace('.',',')):
                            is_this_block_valid = False
                            block_error_details.append(f"结束时间毫秒位数错误: \"{tc_end_str}\"。")

                        if is_this_block_valid:
                            current_start_ms = timecode_to_milliseconds_normalized(tc_start_str)
                            current_end_ms = timecode_to_milliseconds_normalized(tc_end_str)
                            if current_start_ms is None or current_end_ms is None:
                                is_this_block_valid = False
                                block_error_details.append(f"时间轴无法安全转为毫秒: '{time_str_original}'。")
                            elif current_start_ms > current_end_ms:
                                is_this_block_valid = False
                                block_error_details.append(f"时间轴内部逻辑错误: 开始时间晚于结束时间。")

                    if is_this_block_valid: # 检查中英文分割
                        found_split = False
                        if len(lines) >= 4: # 确保至少有4行给序号、时间、中文、英文
                            # 尝试找到中英文的合理分割点
                            # 假设中文至少一行 (lines[2]), 英文至少一行 (lines[ch_idx+1])
                            for ch_idx in range(2, len(lines) - 1): # ch_idx 是中文部分的最后一行索引
                                ch_part_candidate = "\n".join(lines[2 : ch_idx + 1])
                                en_part_candidate = "\n".join(lines[ch_idx + 1 :])
                                if ch_part_candidate.strip() and en_part_candidate.strip():
                                    found_split = True
                                    break
                        if not found_split:
                            is_this_block_valid = False
                            block_error_details.append("中英文文本区分错误或部分为空。")

                if is_this_block_valid and all(v is not None for v in [current_seq_val, current_start_ms, current_end_ms]):
                    parsed_blocks_for_sequential_check.append({'seq': current_seq_val, 'start_ms': current_start_ms, 'end_ms': current_end_ms, 'block_text': block_text, 'original_index': i + 1})
                elif is_this_block_valid:
                     is_this_block_valid = False # Mark as invalid if necessary data wasn't extracted
                     block_error_details.append("内部解析错误：必要数据（序号或时间毫秒）未能提取。")

                if not is_this_block_valid: # If any check failed for this block
                    report = f"格式不符 - 字幕块 {i+1}:\n---\n{block_text}\n---\n  问题:\n" + "\n".join(f"    - {d}" for d in block_error_details)
                    check_errors.append(report)
            # --- 单块检查结束 ---

            # --- 跨块序列检查 (仅当没有单块错误时) ---
            if not check_errors and parsed_blocks_for_sequential_check:
                parsed_blocks_for_sequential_check.sort(key=lambda x: x['seq'])
                for idx in range(len(parsed_blocks_for_sequential_check)): # Iterate all blocks for first item check
                    curr_b = parsed_blocks_for_sequential_check[idx]
                    if idx == 0: # Check if the very first sequence number is 1 (or other expected start)
                        if curr_b['seq'] != 1: # Assuming subtitles should start from 1
                             check_errors.append(f"序号逻辑错误：第一个字幕序号不是 1 (实际为 {curr_b['seq']}, 原块 {curr_b['original_index']})。")
                    if idx > 0:
                        prev_b = parsed_blocks_for_sequential_check[idx-1]
                        if curr_b['seq'] != prev_b['seq'] + 1:
                            check_errors.append(f"序号不连续/非递增：从序号 {prev_b['seq']} (原块 {prev_b['original_index']}) 跳至序号 {curr_b['seq']} (原块 {curr_b['original_index']})。")
                        if prev_b['end_ms'] > curr_b['start_ms']:
                            check_errors.append(
                                f"时间轴顺序逻辑错误：序号 {curr_b['seq']} (原块 {curr_b['original_index']}) 的开始时间 ({milliseconds_to_timecode(curr_b['start_ms'])}) "
                                f"早于前一个序号 {prev_b['seq']} (原块 {prev_b['original_index']}) 的结束时间 ({milliseconds_to_timecode(prev_b['end_ms'])})."
                            )
            # --- 跨块检查结束 ---

        if not check_errors and blocks:
            check_success_message = "所有被检查的字幕段落格式、序号和时间逻辑均符合要求！"
            show_confetti = True
        elif not blocks and not check_errors: # Input was empty or only whitespace
            check_errors.append("输入内容未包含有效字幕块。")

    return jsonify({
        "status": "success" if not check_errors else "error",
        "message": check_success_message,
        "errors": check_errors,
        "show_confetti": show_confetti,
        "confetti_trigger": "checkFormatButtonAjax" if show_confetti else None # Use AJAX button ID
    })

# --- AJAX 符号检查路由 (/ajax_check_symbols) ---
# (与您当前文档中的版本相同，此处省略以保持简洁)
@app.route('/ajax_check_symbols', methods=['POST'])
def ajax_check_symbols():
    data = request.get_json()
    check_input_text = data.get('check_content', '')
    check_errors = []
    check_success_message = ""
    show_confetti = False

    if not check_input_text.strip():
        check_errors.append("用于符号检查的输入内容为空。")
    else:
        normalized_content = check_input_text.replace('\r\n', '\n').strip()
        if not normalized_content.endswith("\n\n"): normalized_content += "\n\n"
        blocks = [block.strip() for block in normalized_content.split('\n\n') if block.strip()]

        found_any_symbols_overall = False
        if not blocks:
            check_errors.append("未检测到有效的字幕段落用于符号检查。")
        else:
            for i, block_text in enumerate(blocks):
                lines = block_text.split('\n')
                seq_num_info = f"序号 {lines[0].strip()}" if lines and lines[0].strip().isdigit() else f"字幕块 {i+1}"
                block_has_symbols = False
                symbols_details_for_block = []
                for line_idx, line_content in enumerate(lines):
                    found_symbols_in_line = {char for char in line_content if char in CHINESE_PUNCTUATIONS_TO_FLAG}
                    if found_symbols_in_line:
                        found_any_symbols_overall = True
                        block_has_symbols = True
                        symbols_details_for_block.append(
                            f"  行 {line_idx + 1}: \"{line_content[:60]}{'...' if len(line_content)>60 else ''}\" (发现符号: {' '.join(sorted(list(found_symbols_in_line)))})"
                        )
                if block_has_symbols:
                    check_errors.append(f"{seq_num_info} 检测到指定中文符号:\n" + "\n".join(symbols_details_for_block))

        if not found_any_symbols_overall and blocks:
            check_success_message = "未检测到指定的中文标点符号。"
            show_confetti = True
        elif not blocks and not check_errors:
            check_errors.append("输入内容未包含有效字幕块进行符号检查。")

    return jsonify({
        "status": "success" if not found_any_symbols_overall and blocks else "info",
        "message": check_success_message,
        "errors": check_errors,
        "show_confetti": show_confetti,
        "confetti_trigger": "checkSymbolsButtonAjax" if show_confetti else None # Use AJAX button ID
    })

if __name__ == '__main__':
    app.run(debug=True)
