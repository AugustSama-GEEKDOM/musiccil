// 极简 JSON 解析器：只够本项目用（接口返回的结构很简单）。
//
// 不引入第三方库是刻意的——GUI 要能用一个 g++ 命令直接编出独立 exe，
// 让用户自己去配 vcpkg/CMake 反而把门槛抬高了。
#pragma once

#include <map>
#include <cstdio>
#include <string>
#include <vector>

namespace json {

enum class Type { Null, Bool, Number, String, Array, Object };

class Value {
public:
    Type type = Type::Null;
    bool boolean = false;
    double number = 0.0;
    std::string text;
    std::vector<Value> items;
    std::map<std::string, Value> fields;

    bool isNull() const { return type == Type::Null; }
    bool isString() const { return type == Type::String; }
    bool isNumber() const { return type == Type::Number; }
    bool isArray() const { return type == Type::Array; }
    bool isObject() const { return type == Type::Object; }

    // 取值辅助：字段缺失或类型不符时返回默认值，调用处不用到处判空。
    std::string str(const std::string& key, const std::string& fallback = "") const {
        std::map<std::string, Value>::const_iterator it = fields.find(key);
        if (it == fields.end()) return fallback;
        if (it->second.type == Type::String) return it->second.text;
        if (it->second.type == Type::Number) return num2str(it->second.number);
        if (it->second.type == Type::Null) return fallback;
        return fallback;
    }

    const Value& at(const std::string& key) const {
        static const Value empty;
        std::map<std::string, Value>::const_iterator it = fields.find(key);
        return it == fields.end() ? empty : it->second;
    }

    static std::string num2str(double v) {
        char buf[64];
        if (v == (double)(long long)v) snprintf(buf, sizeof(buf), "%lld", (long long)v);
        else snprintf(buf, sizeof(buf), "%g", v);
        return buf;
    }
};

// 把 UTF-8 字节串解析成 Value。失败时 ok=false。
Value parse(const std::string& input, bool* ok = 0);

std::string escape(const std::string& s);

}  // namespace json
