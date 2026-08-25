// AiboxPpidController.hpp — pid1.cpp P_PID 等价重建，不混入自定义 PID。
#pragma once
#include <algorithm>
#include <cmath>
namespace ttbox::core::aim {
class AiboxPpidController {
public:
 void init(double kp,double kd,double predict,double rate,double smooth){kp_=kp;kd_=kd;predict_=predict;kp_rate_=rate;smooth_=smooth;reset();}
 void reset(){kp_gain_=0;integral_gain_=0;u_=0;last_error_=0;vx_=0;vp_=0;ix_=0;ip_=0;}
 double update(double e){
  if(std::abs(e)<0.3)e=0;
  if(std::abs(e-last_error_)>30.0)reset();
  adjust_integral(e); adjust_kp(e);
  const double diff=e-last_error_;
  double velocity=kalman(vx_,vp_,diff,0.01,1.0);
  double raw=velocity;
  if(std::abs(e)<1.0&&std::abs(diff)<0.1)raw=diff+u_*0.5;
  double ki_raw=(std::abs(raw)>0.5?raw:0.0)*predict_*integral_gain_;
  ki_raw=kalman(ix_,ip_,ki_raw,0.5,1.0);
  double kp=soft(kp_*e,100.0), ki=soft(ki_raw,9000.0), kd=soft(kd_*diff,100.0);
  u_=(kp+ki+kd)*kp_gain_; last_error_=e; return u_;
 }
private:
 double kalman(double& x,double& p,double m,double q,double r){double pp=p+q,k=pp/(pp+r);x=x+k*(m-x);p=(1-k)*pp;return x;}
 void adjust_integral(double e){double a=std::abs(e);if(a<50){double ratio=1-a/50;integral_gain_+=(ratio-integral_gain_)*0.025;}else{double ratio=50/a;integral_gain_+=(ratio*integral_gain_-integral_gain_)*0.1;}integral_gain_=std::clamp(integral_gain_,0.0,1.0);}
 void adjust_kp(double e){double a=std::abs(e);if(a<1920){double ratio=1-a/1920;kp_gain_+=(ratio-kp_gain_)*kp_rate_;}else{double ratio=1920/a;kp_gain_+=(ratio*kp_gain_-kp_gain_)*0.1;}kp_gain_=std::clamp(kp_gain_,0.0,1.0);}
 double soft(double v,double scale)const{double r=v/10000.0,s=r*r;return r*(1.0+(4.0/15.0)*s)/(1.0+(3.0/5.0)*s)*scale;}
 double kp_=25,kd_=25,predict_=0,kp_rate_=0.3,smooth_=9900;double kp_gain_=0,integral_gain_=0,u_=0,last_error_=0;double vx_=0,vp_=0,ix_=0,ip_=0;
};}
