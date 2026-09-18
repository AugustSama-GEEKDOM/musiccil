#include "json.h"

namespace json {
namespace {

struct Parser {
    const std::string& s;
    size_t i = 0;
    bool bad = false;

    explicit Parser(const std::string& src) : s(src) {}

    void skip() {
        while (i < s.size()) {
            char c = s[i];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') { i++; continue; }
            break;
        }
    }

    bool eat(char c) {
        skip();
        if (i < s.size() && s[i] == c) { i++; return true; }
        return false;
    }

    // UTF-8 编码一个码点
    static void putUtf8(std::string& out, unsigned cp) {
        if (cp < 0x80) {
            out += (char)cp;
        } else if (cp < 0x800) {
            out += (char)(0xC0 | (cp >> 6));
            out += (char)(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += (char)(0xE0 | (cp >> 12));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        } else {
            out += (char)(0xF0 | (cp >> 18));
            out += (char)(0x80 | ((cp >> 12) & 0x3F));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        }
    }

    unsigned hex4() {
        unsigned v = 0;
        for (int k = 0; k < 4 && i < s.size(); k++, i++) {
            char c = s[i];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
            else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
            else { bad = true; return 0; }
        }
        return v;
    }

    std::string parseString() {
        std::string out;
        if (!eat('"')) { bad = true; return out; }
        while (i < s.size()) {
            char c = s[i++];
            if (c == '"') return out;
            if (c != '\\') { out += c; continue; }
            if (i >= s.size()) break;
            char e = s[i++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = hex4();
                    // 代理对：把低位一起吃掉，拼成一个完整码点
                    if (cp >= 0xD800 && cp <= 0xDBFF && i + 1 < s.size() &&
                        s[i] == '\\' && s[i + 1] == 'u') {
                        i += 2;
                        unsigned lo = hex4();
                        if (lo >= 0xDC00 && lo <= 0xDFFF)
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    putUtf8(out, cp);
                    break;
                }
                default: out += e; break;
            }
        }
        bad = true;
        return out;
    }

    Value parseValue() {
        skip();
        Value v;
        if (i >= s.size()) { bad = true; return v; }
        char c = s[i];
        if (c == '{') {
            i++;
            v.type = Type::Object;
            skip();
            if (eat('}')) return v;
            while (i < s.size()) {
                std::string key = parseString();
                if (bad) return v;
                if (!eat(':')) { bad = true; return v; }
                v.fields[key] = parseValue();
                if (bad) return v;
                if (eat(',')) continue;
                if (eat('}')) return v;
                bad = true;
                return v;
            }
            bad = true;
            return v;
        }
        if (c == '[') {
            i++;
            v.type = Type::Array;
            skip();
            if (eat(']')) return v;
            while (i < s.size()) {
                v.items.push_back(parseValue());
                if (bad) return v;
                if (eat(',')) continue;
                if (eat(']')) return v;
                bad = true;
                return v;
            }
            bad = true;
            return v;
        }
        if (c == '"') {
            v.type = Type::String;
            v.text = parseString();
            return v;
        }
        if (s.compare(i, 4, "true") == 0) { i += 4; v.type = Type::Bool; v.boolean = true; return v; }
        if (s.compare(i, 5, "false") == 0) { i += 5; v.type = Type::Bool; v.boolean = false; return v; }
        if (s.compare(i, 4, "null") == 0) { i += 4; v.type = Type::Null; return v; }
        // 数字
        size_t start = i;
        if (i < s.size() && (s[i] == '-' || s[i] == '+')) i++;
        bool any = false;
        while (i < s.size() && ((s[i] >= '0' && s[i] <= '9') || s[i] == '.' ||
                                s[i] == 'e' || s[i] == 'E' || s[i] == '-' || s[i] == '+')) {
            any = true;
            i++;
        }
        if (!any) { bad = true; return v; }
        v.type = Type::Number;
        v.number = atof(s.substr(start, i - start).c_str());
        return v;
    }
};

}  // namespace

Value parse(const std::string& input, bool* ok) {
    Parser p(input);
    Value v = p.parseValue();
    if (ok) *ok = !p.bad;
    if (p.bad) return Value();
    return v;
}

std::string escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (size_t k = 0; k < s.size(); k++) {
        unsigned char c = (unsigned char)s[k];
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
    return out;
}

}  // namespace json
