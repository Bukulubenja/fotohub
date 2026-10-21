from django.urls import path

from shop import views

urlpatterns = [
    path("<str:token>/cart/", views.cart_page, name="shop-cart"),
    path("<str:token>/cart/add/", views.cart_add, name="shop-cart-add"),
    path("<str:token>/cart/add-all/", views.cart_add_all, name="shop-cart-add-all"),
    path("<str:token>/cart/remove/", views.cart_remove, name="shop-cart-remove"),
    path("<str:token>/cart/checkout/", views.checkout, name="shop-checkout"),
]
