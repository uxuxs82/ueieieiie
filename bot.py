import os
import re
import time
import json
import subprocess
import requests
from threading import Lock, Thread

BOT_TOKEN = "8908574849:AAG6SXKhIAfsnt3uTnJeHI8_GVMXWxwMBGA"
WORK_DIR = os.getcwd()
os.chdir(WORK_DIR)

RATE_LIMIT = 5
LAST_CMD = {}
LOCK = Lock()

# -------------------------- ВШИТАЯ ДОКУМЕНТАЦИЯ UXT --------------------------
MEGA_PROMPT = """
Ты — ИИ-агент, управляющий языком uxt.
У тебя есть доступ к файлам. Ты можешь читать, писать, выполнять команды, собирать проект.

=== ЯЗЫК UXT (полный синтаксис) ===
Команды:
- print текст с $переменными – вывод
- input приглашение, переменная – ввод
- переменная = значение – присваивание
- if условие then ... else ... end – условие
- loop число then ... end – цикл
- read файл переменная – чтение файла
- write файл, данные – запись файла
- fetch url переменная – HTTP запрос
- json переменная_с_json путь новая_переменная – парсинг json
- shell команда – системная команда

Правила:
- текст без кавычек
- числа без W
- переменные в print через $ (print $name)
- блоки заканчиваются end
- сравнение через = (не ==)
- запятая в input: input Как тебя зовут, name

Примеры:
print привет
input как тебя зовут, name
print привет $name
if age >= 18 then print взрослый else print ребенок end
loop 5 then print привет end
write data.txt, привет
read data.txt content
print $content
fetch https://api.ipify.org ip
print $ip
json data ip ip
shell date

=== ПОЛНЫЙ ИСХОДНИК ИНТЕРПРЕТАТОРА (uxt.c) ===
Код на C, использует libcurl. Ты можешь его читать, править, добавлять команды, фиксить баги.

uxt.c:
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <curl/curl.h>
#include <math.h>
#include <time.h>

typedef struct Var { char *name, *val; struct Var *next; } Var;
Var *vars = NULL;

void set_var(const char *name, const char *val) {
    Var *v = vars;
    while (v) { if (!strcmp(v->name, name)) { free(v->val); v->val = strdup(val); return; } v = v->next; }
    Var *n = malloc(sizeof(Var)); n->name = strdup(name); n->val = strdup(val); n->next = vars; vars = n;
}
char* get_var(const char *name) {
    Var *v = vars; while (v) { if (!strcmp(v->name, name)) return v->val; v = v->next; } return NULL;
}
void trim(char *s) { char *p = s; int l = strlen(p); while (l>0 && isspace(p[l-1])) p[--l]=0; while (*p && isspace(*p)) ++p,--l; memmove(s,p,l+1); }
int is_num(const char *s) { if (*s=='W') s++; if (!*s) return 0; while (*s) if (!isdigit(*s) && *s!='.') return 0; else s++; return 1; }
double parse_num(const char *s) { if (*s=='W') s++; return atof(s); }

char* interpolate(const char *s) {
    char *r = malloc(1); r[0]=0;
    while (*s) {
        if (*s == '$') { s++; char name[256]; int i=0; while (isalnum(*s)||*s=='_') name[i++]=*s++; name[i]=0; char *v=get_var(name); if(!v) v="[undef]"; r=realloc(r,strlen(r)+strlen(v)+1); strcat(r,v); }
        else { r=realloc(r,strlen(r)+2); strncat(r,s,1); s++; }
    }
    return r;
}

size_t write_cb(void *p, size_t sz, size_t n, void *d) { strcat((char*)d, (char*)p); return sz*n; }
char* http_get(const char *url) {
    CURL *c = curl_easy_init(); char *resp = malloc(1); resp[0]=0;
    if (!c) return resp;
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, resp);
    curl_easy_perform(c); curl_easy_cleanup(c);
    return resp;
}

int eval_cond(const char *cond) {
    char op[4]="", left[256]="", right[256]="";
    const char *p = cond;
    while (*p && !isspace(*p) && !strchr("=!<>",*p)) p++;
    strncpy(left, cond, p-cond); left[p-cond]=0; trim(left);
    while (isspace(*p)) p++; strncpy(op, p, 2); p += 2;
    while (isspace(*p)) p++; strcpy(right, p); trim(right);
    double l = (left[0]=='W')?parse_num(left):atof(get_var(left)?:left);
    double r = (right[0]=='W')?parse_num(right):atof(get_var(right)?:right);
    if (!strcmp(op,"==")) return l==r;
    if (!strcmp(op,"!=")) return l!=r;
    if (!strcmp(op,">=")) return l>=r;
    if (!strcmp(op,"<=")) return l<=r;
    if (!strcmp(op,">")) return l>r;
    if (!strcmp(op,"<")) return l<r;
    return 0;
}

void execute_file(const char *fname);
void execute_line(char *line, FILE *f, int *ln) {
    trim(line); if (!*line || (line[0]=='/' && line[1]=='/')) return;
    if (!strncmp(line,"print",5) && isspace(line[5])) { char *p=line+5; while(isspace(*p))p++; char *out=interpolate(p); printf("%s\\n",out); free(out); return; }
    if (!strncmp(line,"input",5) && isspace(line[5])) {
        char *p=line+5; while(isspace(*p))p++;
        char *comma = strchr(p,','); char *varname; char *prompt=NULL;
        if (comma) { prompt=malloc(comma-p+1); strncpy(prompt,p,comma-p); prompt[comma-p]=0; trim(prompt); varname=comma+1; trim(varname); }
        else varname=p, trim(varname);
        if (prompt) printf("%s", prompt);
        char val[1024]; fgets(val,1024,stdin); val[strcspn(val,"\\n")]=0;
        set_var(varname, val);
        free(prompt); return;
    }
    if (strchr(line,'=')) {
        char *eq = strchr(line,'='); char *name=malloc(eq-line+1); strncpy(name,line,eq-line); name[eq-line]=0; trim(name);
        char *val=eq+1; trim(val);
        set_var(name, val);
        free(name); return;
    }
    if (!strncmp(line,"if",2) && isspace(line[2])) {
        char *cond = line+2; while(isspace(*cond)) cond++;
        char *then_pos = strstr(cond, " then");
        if (!then_pos) return;
        *then_pos = 0;
        char *p = cond; while (isspace(*p)) p++;
        int res = eval_cond(p);
        int skip = !res, nested = 0;
        char sub[8192];
        while (fgets(sub,sizeof(sub),f)) {
            trim(sub);
            if (!strncmp(sub,"if",2) && isspace(sub[2])) nested++;
            if (!strcmp(sub,"end") && nested==0) break;
            if (!strncmp(sub,"else",4) && nested==0) { skip = !skip; continue; }
            if (!skip && nested==0) execute_line(sub,f,NULL);
        }
        return;
    }
    if (!strncmp(line,"loop",4) && isspace(line[4])) {
        char *p = line+4; while(isspace(*p)) p++;
        int n = atoi(p);
        long pos = ftell(f);
        for (int i=1; i<=n; i++) {
            char buf[32]; sprintf(buf, "%d", i);
            set_var("it", buf);
            fseek(f,pos,SEEK_SET);
            int nested=0; char sub[8192];
            while (fgets(sub,sizeof(sub),f)) {
                trim(sub);
                if (!strcmp(sub,"end") && nested==0) break;
                if (!strncmp(sub,"loop",4) && isspace(sub[4])) nested++;
                if (nested==0) execute_line(sub,f,NULL);
            }
        }
        int nested=0; char sub[8192];
        while (fgets(sub,sizeof(sub),f)) {
            trim(sub);
            if (!strcmp(sub,"end") && nested==0) break;
            if (!strncmp(sub,"loop",4) && isspace(sub[4])) nested++;
        }
        return;
    }
    if (!strncmp(line,"read",4) && isspace(line[4])) {
        char *p=line+4; while(isspace(*p))p++;
        if (*p=='(') p++; if (*p=='"') p++;
        char fname[1024]; strcpy(fname,p);
        char *end = strrchr(fname,'"'); if(end)*end=0;
        char *cp = strrchr(fname,')'); if(cp)*cp=0;
        FILE *f2=fopen(fname,"r"); if(!f2){set_var("_","");return;}
        fseek(f2,0,SEEK_END); long sz=ftell(f2); fseek(f2,0,SEEK_SET);
        char *buf=malloc(sz+1); fread(buf,1,sz,f2); buf[sz]=0; fclose(f2);
        set_var("_",buf); free(buf); return;
    }
    if (!strncmp(line,"write",5) && isspace(line[5])) {
        char *p=line+5; while(isspace(*p))p++;
        if (*p=='(') p++;
        char *comma=strchr(p,','); if(!comma)return;
        char fname[1024]; strncpy(fname,p,comma-p); fname[comma-p]=0; trim(fname);
        if(fname[0]=='"'){fname[strlen(fname)-1]=0; memmove(fname,fname+1,strlen(fname));}
        p=comma+1; while(isspace(*p))p++;
        if(*p=='"')p++;
        char data[8192]; strcpy(data,p);
        char *end=strrchr(data,'"'); if(end)*end=0;
        char *cp=strrchr(data,')'); if(cp)*cp=0;
        FILE *f2=fopen(fname,"w"); if(f2){fprintf(f2,"%s",data); fclose(f2);}
        return;
    }
    if (!strncmp(line,"shell",5) && isspace(line[5])) {
        char *p=line+5; while(isspace(*p))p++;
        system(p); return;
    }
    if (!strncmp(line,"fetch",5) && isspace(line[5])) {
        char *p=line+5; while(isspace(*p))p++;
        if (*p=='(') p++; if (*p=='"') p++;
        char url[1024]; strcpy(url,p);
        char *end=strrchr(url,'"'); if(end)*end=0;
        char *cp=strrchr(url,')'); if(cp)*cp=0;
        char *resp = http_get(url);
        set_var("_", resp);
        free(resp); return;
    }
    if (!strncmp(line,"json",4) && isspace(line[4])) {
        char *p=line+4; while(isspace(*p))p++;
        char *src_var=p; while(*p && !isspace(*p)) p++; *p=0; p++;
        while(isspace(*p)) p++;
        char *path=p; while(*p && !isspace(*p)) p++; *p=0; p++;
        while(isspace(*p)) p++;
        char *dest_var=p; trim(src_var); trim(path); trim(dest_var);
        char *json_str=get_var(src_var); if(!json_str) json_str="";
        static char res[256]; res[0]=0;
        char search[512]; sprintf(search, "\"%s\"", path);
        char *key_pos = strstr(json_str, search);
        if(key_pos) {
            key_pos += strlen(search);
            while(*key_pos && *key_pos!=':') key_pos++;
            if(*key_pos==':') key_pos++;
            while(isspace(*key_pos)) key_pos++;
            if(*key_pos=='"') {
                key_pos++;
                char *end = strchr(key_pos, '"');
                if(end) { strncpy(res, key_pos, end-key_pos); res[end-key_pos]=0; }
            } else {
                char *end = key_pos;
                while(*end && !strchr(",} \t\n", *end)) end++;
                strncpy(res, key_pos, end-key_pos); res[end-key_pos]=0;
            }
        }
        set_var(dest_var, res);
        return;
    }
}
void execute_file(const char *fname) {
    FILE *f = fopen(fname, "r");
    if (!f) { printf("Cannot open %s\n", fname); return; }
    char line[8192];
    while (fgets(line,sizeof(line),f)) execute_line(line,f,NULL);
    fclose(f);
}
int main(int argc, char **argv) {
    if (argc < 2) { printf("Usage: uxt script.uxt\n"); return 1; }
    execute_file(argv[1]);
    return 0;
}

=== ИНСТРУКЦИЯ ДЛЯ ТЕБЯ ===
- ЧИТАТЬ: путь – прочитать файл
- ПИСАТЬ: путь (потом СОДЕРЖИМОЕ: ...) – записать
- ВЫПОЛНИТЬ: команда – shell
- СОБРАТЬ: – скомпилировать uxt.c
- ОТВЕТ: текст – просто ответить
"""

# -------------------------- АВТОУСТАНОВКА OLLAMA + МОДЕЛИ --------------------------
def setup_ollama():
    print("[ollama] проверка установки...")
    try:
        subprocess.run(["ollama", "--version"], capture_output=True, check=True)
        print("[ollama] уже установлена")
    except:
        print("[ollama] не найдена, устанавливаю...")
        os.system("curl -fsSL https://ollama.com/install.sh | sh")
        try:
            subprocess.run(["ollama", "--version"], capture_output=True, check=True)
            print("[ollama] установлена успешно")
        except:
            print("[ollama] не удалось установить")
            return False

    # запускаем ollama serve, если не запущена
    if not os.system("pgrep -x ollama > /dev/null"):
        print("[ollama] serve уже запущен")
    else:
        print("[ollama] запускаю serve...")
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)

    # скачиваем лёгкую модель neolinschen/Qwen-Thinking:270m (~200 МБ)
    model = "neolinschen/Qwen-Thinking:270m"
    print(f"[ollama] проверка модели {model}...")
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        if model not in result.stdout:
            print(f"[ollama] модель {model} не найдена, скачиваю...")
            subprocess.run(["ollama", "pull", model], check=True)
            print("[ollama] модель загружена")
        else:
            print("[ollama] модель уже есть")
    except Exception as e:
        print(f"[ollama] ошибка: {e}")
        return False
    return True

setup_ollama()  # запускаем при старте

# -------------------------- ТЕЛЕГРАМ-ФУНКЦИИ --------------------------
def send_message(chat_id, text, reply_markup=None):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"ошибка отправки: {e}")

def send_document(chat_id, filename, caption=""):
    with open(filename, "rb") as f:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                      data={"chat_id": chat_id, "caption": caption},
                      files={"document": f}, timeout=10)

def get_updates(offset):
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                         params={"offset": offset, "timeout": 10}, timeout=10)
        return r.json().get("result", [])
    except:
        return []

# -------------------------- ФАЙЛЫ И SHELL --------------------------
def read_file(path):
    full = os.path.join(WORK_DIR, path)
    if not os.path.exists(full):
        return "файл не найден"
    with open(full, "r") as f:
        return f.read()

def write_file(path, content):
    full = os.path.join(WORK_DIR, path)
    with open(full, "w") as f:
        f.write(content)
    return f"файл {path} сохранён"

def exec_shell(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=WORK_DIR)
        return (r.stdout + r.stderr).strip() or "выполнено"
    except Exception as e:
        return f"ошибка: {e}"

def build():
    return exec_shell("gcc -o uxt uxt.c -lcurl -lm -Wall -O2 2>&1")

# -------------------------- ИИ (API + LOCAL FALLBACK) --------------------------
def ask_local_ollama(prompt):
    model = "neolinschen/Qwen-Thinking:270m"
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": model,
        "prompt": MEGA_PROMPT + "\n\n" + prompt,
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 1200
    }
    try:
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("response", "")
        else:
            return None
    except:
        return None

def ask_api_ollama(prompt):
    models = ["deepseek", "mistral", "gpt-4o-mini", "claude", "gemini", "llama"]
    url = "https://api.pollinations.ai/v1/chat/completions"
    for model in models:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": MEGA_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 1200
            }
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except:
            continue
    return None

def ask_ai(prompt):
    # сначала пробуем API
    result = ask_api_ollama(prompt)
    if result:
        return result
    # если API не работает, пробуем локальную модель
    local = ask_local_ollama(prompt)
    if local:
        return local
    return "ОТВЕТ: все модели (и API, и локальная) временно недоступны. проверь интернет и локальную ollama."

# -------------------------- ОБРАБОТКА КОМАНД --------------------------
def handle_ai_response(chat_id, response):
    lines = response.splitlines()
    if not lines:
        send_message(chat_id, "пустой ответ")
        return
    first = lines[0].strip()
    if first.startswith("ЧИТАТЬ:"):
        path = first.split(":",1)[1].strip()
        content = read_file(path)
        if len(content)>1500:
            send_document(chat_id, path, path)
        else:
            send_message(chat_id, f"```\n{content}\n```")
    elif first.startswith("ПИСАТЬ:"):
        path = first.split(":",1)[1].strip()
        in_content=False
        content=[]
        for line in lines[1:]:
            if "СОДЕРЖИМОЕ:" in line:
                in_content=True
                continue
            if in_content and "СОБРАТЬ:" not in line:
                content.append(line)
        code="\n".join(content)
        res=write_file(path, code)
        send_message(chat_id, res)
        if any("СОБРАТЬ:" in l for l in lines):
            build_res=build()
            send_message(chat_id, f"сборка:\n```\n{build_res[:1500]}\n```")
    elif first.startswith("ВЫПОЛНИТЬ:"):
        cmd=first.split(":",1)[1].strip()
        res=exec_shell(cmd)
        send_message(chat_id, f"```\n{res[:1500]}\n```")
    elif first.startswith("СОБРАТЬ:"):
        res=build()
        send_message(chat_id, f"```\n{res[:1500]}\n```")
    elif first.startswith("ОТВЕТ:"):
        send_message(chat_id, first.split(":",1)[1].strip())
    else:
        send_message(chat_id, response[:2000])

def handle(chat_id, text):
    if text.startswith("/start"):
        send_message(chat_id, "бот готов. пиши любой запрос или /help для помощи")
    elif text.startswith("/help"):
        send_message(chat_id, "команды: /start, /help, /fs list, /fs read файл, /fs write файл код")
    elif text.startswith("/fs"):
        parts = text[4:].strip().split()
        if not parts:
            send_message(chat_id, "/fs list, /fs read файл, /fs write файл код")
            return
        if parts[0] == "list":
            files = "\n".join(os.listdir(WORK_DIR))
            send_message(chat_id, f"```\n{files}\n```")
        elif parts[0] == "read" and len(parts) > 1:
            content = read_file(parts[1])
            if len(content) > 1500:
                send_document(chat_id, parts[1], parts[1])
            else:
                send_message(chat_id, f"```\n{content}\n```")
        elif parts[0] == "write" and len(parts) > 1:
            path = parts[1]
            content = " ".join(parts[2:])
            send_message(chat_id, write_file(path, content))
        else:
            send_message(chat_id, "неизвестная подкоманда")
    else:
        send_message(chat_id, "думаю...")
        resp = ask_ai(text)
        handle_ai_response(chat_id, resp)

# -------------------------- ГЛАВНЫЙ ЦИКЛ --------------------------
def main():
    print("бот запущен, папка:", WORK_DIR)
    offset = 0
    while True:
        updates = get_updates(offset)
        for upd in updates:
            if "message" in upd:
                chat_id = upd["message"]["chat"]["id"]
                text = upd["message"].get("text", "")
                if text:
                    # ограничение по частоте
                    now = time.time()
                    if chat_id in LAST_CMD and now - LAST_CMD[chat_id] < RATE_LIMIT:
                        send_message(chat_id, f"подожди {RATE_LIMIT - int(now - LAST_CMD[chat_id])} сек")
                        continue
                    LAST_CMD[chat_id] = now
                    handle(chat_id, text)
                offset = upd["update_id"] + 1
        time.sleep(2)

if __name__ == "__main__":
    main()
