// AlphaBetaGammaFilter.hpp — 目标位置/速度/加速度预测。
#pragma once
#include <algorithm>
namespace ttbox::core::aim {
class AlphaBetaGammaFilter {
public:
    struct State { float x=0,y=0,vx=0,vy=0,ax=0,ay=0; bool initialized=false; };
    void configure(float alpha,float beta,float gamma,float predict_ms) { a_=std::clamp(alpha,0.0f,1.0f); b_=std::max(0.0f,beta); g_=std::max(0.0f,gamma); predict_ms_=std::max(0.0f,predict_ms); }
    void reset(){s_={};}
    void update(float mx,float my,float dt){
        if(dt<=0.0001f||dt>1.0f) dt=0.004f;
        if(!s_.initialized){s_.x=mx;s_.y=my;s_.initialized=true;return;}
        const float px=s_.x+s_.vx*dt+0.5f*s_.ax*dt*dt, py=s_.y+s_.vy*dt+0.5f*s_.ay*dt*dt;
        const float vx=s_.vx+s_.ax*dt, vy=s_.vy+s_.ay*dt;
        const float rx=mx-px, ry=my-py;
        s_.x=px+a_*rx; s_.y=py+a_*ry; s_.vx=vx+b_*rx/dt; s_.vy=vy+b_*ry/dt;
        s_.ax += g_*rx/(0.5f*dt*dt); s_.ay += g_*ry/(0.5f*dt*dt);
    }
    State predicted() const { const float t=predict_ms_/1000.0f; State o=s_; o.x=s_.x+s_.vx*t+0.5f*s_.ax*t*t; o.y=s_.y+s_.vy*t+0.5f*s_.ay*t*t; return o; }
private: State s_{}; float a_=0.8f,b_=0.3f,g_=0.1f,predict_ms_=50.0f;
};
}
