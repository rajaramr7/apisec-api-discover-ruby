Rails.application.routes.draw do
  root 'pages#home'

  # Basic resources
  resources :posts do
    member do
      post :publish
    end
    collection do
      get :drafts
    end
    resources :comments, only: [:create, :destroy]
  end

  # Singular resource
  resource :profile, only: [:show, :edit, :update]

  # Namespace
  namespace :api do
    namespace :v1 do
      resources :users, except: [:new, :edit]
      resources :sessions, only: [:create, :destroy]
    end
  end

  # Admin namespace
  namespace :admin do
    resources :users
    resources :settings, only: [:index, :update]
  end

  # Scope with module
  scope '/internal', module: :internal do
    resources :reports, only: [:index, :show]
  end

  # Direct HTTP verb routes
  get '/health', to: 'health#check'
  post '/webhooks/stripe', to: 'webhooks#stripe'
  delete '/cache', to: 'admin#clear_cache'

  # Match with multiple verbs
  match '/search', to: 'search#index', via: [:get, :post]

  # Mount engine
  mount Sidekiq::Web => '/sidekiq'

  # Concern
  concern :commentable do
    resources :comments, only: [:index, :create]
  end

  resources :articles, concerns: [:commentable]

  # Conditional route
  if Rails.env.development?
    get '/debug', to: 'debug#index'
  end

  # Draw from external file
  draw(:legacy)

  # Scope with controller
  scope '/payments', controller: :payments do
    post :checkout
    get :status
  end

  # with_options
  with_options controller: :pages do
    get :about
    get :terms
  end
end
