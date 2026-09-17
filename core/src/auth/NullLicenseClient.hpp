// core/src/auth/NullLicenseClient.hpp —— M1 空在线客户端（T1.07）
//
// 存在意义：让 Application 在"无在线授权客户端"时不依赖任何 Aibox/Ttbox 实体，
// 从而彻底断开这两类头文件默认成员初始化器的 ODR-use（见 t1.07-impl-spec.md §5）。
// 语义与 AUTH=OFF 的既有桩一致：不访问网络，恒返回 kNetworkError（网络类失败），
// 交由 LicenseDaemon(T1.08 状态机) 走 fail-open（kFallback）。
//
// ★ 本类必须零字面量：不得含任何 URL / IP / key 常量（它存在的目的就是"消除"字面量）；
//   纳入 T1.15 字符串门禁覆盖范围。
#pragma once

#include <string>

#include "auth/LicenseDaemon.hpp"   // ILicenseClient / LicenseStatus / LicenseState

namespace ttbox::core::auth {

class NullLicenseClient : public ILicenseClient {
public:
    NullLicenseClient() = default;
    ~NullLicenseClient() override = default;

    bool verify_once(const std::string& /*card*/,
                     const std::string& /*bind_device*/,
                     LicenseStatus& out_status,
                     std::string* err_message = nullptr) override {
        out_status = {};
        out_status.state = LicenseState::kNetworkError;
        if (err_message) *err_message = "M1: 无在线授权客户端（NullLicenseClient）";
        return false;
    }
};

}  // namespace ttbox::core::auth
